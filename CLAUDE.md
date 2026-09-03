# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Qué es este repo

Integración personalizada de Home Assistant (distribuida vía HACS) que consulta la API privada de clientes de [Niba](https://niba.es): consumo eléctrico del período en curso, historial de facturas y saldo del monedero / batería solar.

No hay API pública: la autenticación se hace con un JWT que el usuario copia del navegador y que se envía como cabecera `authorization: token <JWT>`.

## Entorno y comandos

Las pruebas necesitan Home Assistant instalado, vía `pytest-homeassistant-custom-component`. El entorno vive en `.venv/` (ignorado por git):

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

.venv/bin/python -m pytest -q                                  # toda la suite
.venv/bin/python -m pytest tests/test_niba_coordinator.py -q   # un archivo
.venv/bin/python -m pytest -k accumulated -q                   # por nombre
.venv/bin/ruff check custom_components tests
.venv/bin/ruff format custom_components tests
```

`pytest.ini` usa `pythonpath = .` y `asyncio_mode = auto`, así que los tests importan `from custom_components.niba.api import ...` y las corrutinas no necesitan marcador. `tests/conftest.py` carga el plugin de HA y activa `enable_custom_integrations`.

CI en `.github/workflows/`: `tests.yml` (ruff + pytest en 3.12 y 3.13) y `validate.yml` (hassfest + HACS action). Los dos deben pasar antes de publicar una versión. Ambas validaciones se pueden correr en local con Docker, sin esperar al push:

```bash
docker run --rm -v "$PWD://github/workspace" ghcr.io/home-assistant/hassfest
docker run --rm -e INPUT_GITHUB_TOKEN="$(gh auth token)" -e INPUT_CATEGORY=integration \
  -e GITHUB_REPOSITORY=eclyptox/niba-ha ghcr.io/hacs/action:main
```

Ojo: la action de HACS valida la rama **remota**, no el working tree, así que hay que pushear antes de fiarse de su resultado. El check `brands` está en `ignore` porque exige un PR a `home-assistant/brands`.

## Restricción: `api.py` no depende de Home Assistant

`api.py` no importa nada de HA — en lugar de `aiohttp.ClientSession` define los `Protocol` `HttpSession` / `HttpResponse` y `NibaApiClient` recibe la sesión por inyección. `__init__.py` importa HA de forma diferida (`TYPE_CHECKING` y dentro de `async_setup_entry`).

Esto mantiene `tests/test_niba_api.py` y `test_niba_client.py` ejecutables sin HA y hace triviales los dobles de prueba. Al añadir lógica, prefiere ponerla en `api.py` (parsers, normalizadores, propiedades derivadas de `NibaData`) antes que en el coordinator o los sensores.

## Arquitectura

Flujo de datos: `config_flow` (token + CUPS) → `ConfigEntry` → `NibaCoordinator` → `NibaData` → sensores. El coordinator se guarda en `entry.runtime_data`, no en `hass.data`.

**`api.py`** — cliente y parsers. Todos los modelos son `@dataclass(frozen=True)` y conservan el payload original en `.raw`. El parseo es defensivo por diseño porque la API es privada y no versionada:

- `_float_or_none` acepta el formato de tupla monetaria de Niba (`[importe, "EUR"]`) además de números y cadenas.
- `normalize_cups` recorta el CUPS a los primeros 20 caracteres (mayúsculas, sin espacios): el backend devuelve `value_error.cups_not_found` con el CUPS completo de 22 dígitos. Se aplica en el config flow, en el coordinator y de nuevo dentro de `get_bills` / `get_consumption_period`, de modo que también quedan cubiertas las entradas ya guardadas con el valor largo.
- `decode_token` / `normalize_token` decodifican el JWT **sin verificar la firma**, solo para leer `exp` y `email`; aceptan el valor pegado con o sin el prefijo `token `. La validación real la hace Niba en `/users/me`.
- `_get` traduce los fallos a la jerarquía `NibaApiError` → `NibaAuthError` (401/403) / `NibaPayloadError` (forma inesperada). Esta jerarquía es el contrato con el coordinator y el config flow; cualquier excepción nueva debe heredar de `NibaApiError`.
- `fetch_data` lanza las cuatro peticiones en paralelo con `asyncio.gather`.

Propiedades derivadas en `NibaData`, no en los sensores: `last_bill` (la más reciente por `end_at`, con caída a `bills[0]` si ninguna tiene fecha) y `accumulated_consumption`.

**El acumulado es monotónico a propósito.** `raw_accumulated_consumption` suma facturas + período actual, pero ese valor **decrece** cuando Niba cierra un período: el consumo vuelve a ~0 días antes de que la factura aparezca en `/bills`. HA lee un decremento en un sensor `TOTAL_INCREASING` como reinicio de contador, así que el coordinator realimenta el máximo ya emitido en `NibaData.accumulated_floor` y `accumulated_consumption` devuelve `max(raw, floor)`. El floor sobrevive a reinicios porque `NibaRestoringSensor` lo siembra desde el estado restaurado. Si tocas este camino, mantén los tests de `test_accumulated_total_never_drops_when_a_period_closes`.

**`coordinator.py`** — dos cadencias sobre un único `DataUpdateCoordinator`: el ciclo base es cada `API_REFRESH_MINUTES` (60) y solo cada `BILLS_REFRESH_HOURS` (6) se vuelven a pedir las facturas; el resto de ciclos reutiliza `self._cached_bills`. Al detectar una factura nueva (por `billing_code` o `id`, ignorando la primera carga) crea una notificación persistente y dispara el evento de bus `niba_new_bill`. `NibaAuthError` se convierte en `ConfigEntryAuthFailed` para activar el reauth; el resto en `UpdateFailed`.

**`sensor.py`** — todo sensor declara `state_class`, o Home Assistant no le guarda estadísticas de largo plazo. Ojo con la combinación: `DEVICE_CLASS_STATE_CLASSES` (en `homeassistant/components/sensor/const.py`) admite **solo `TOTAL`** para `device_class=MONETARY`, así que los sensores en euros no pueden usar `MEASUREMENT` aunque representen un saldo. `test_state_classes_are_valid_for_their_device_class` valida cada descripción contra ese mapeo.

Los sensores son declarativos: `NibaSensorDescription` extiende `SensorEntityDescription` con `value_fn(NibaData)`, `extra_attrs_fn(NibaData)` y `restore_floor`. La tupla `SENSORS` es la única fuente de verdad: añadir un sensor = añadir una entrada. `unique_id` es `f"{entry.entry_id}_{description.key}"`, así que **renombrar una `key` rompe el historial de entidades de los usuarios ya instalados**.

**`config_flow.py`** — dos pasos (token → CUPS) más reauth. El `unique_id` es `f"{email}:{cups}"`, de forma que una misma cuenta Niba puede tener varios puntos de suministro; cada entrada es un dispositivo distinto, titulado por su CUPS.

## Al editar

- El mínimo de HA (**2024.11**) se declara **solo en `hacs.json`**: `homeassistant` no es una clave válida de `manifest.json` y hassfest la rechaza (`CUSTOM_INTEGRATION_MANIFEST_SCHEMA` no la contempla). Ese mínimo viene de `_get_reauth_entry()` y `async_update_reload_and_abort(data_updates=...)`, ambos de 2024.11; súbelo si usas una API más nueva.
- El portal guarda el JWT en **`localStorage`, clave `token`** (verificado en el bundle del frontend: `localStorage.getItem("token")`). Los diálogos del config flow y el README explican el procedimiento; si Niba cambia dónde lo guarda, hay que actualizar los tres sitios a la vez.
- **`strings.json` no puede contener URLs literales**: hassfest las rechaza. Pásalas como `description_placeholders` (ver `NIBA_CLIENT_URL` y los `async_show_form` del config flow).
- `strings.json` y `translations/es.json` son **idénticos** y deben mantenerse en sync; toda la UI y los mensajes de usuario están en español.
- Sube `version` en `custom_components/niba/manifest.json` cuando el cambio sea visible para el usuario: es lo que HACS muestra como actualización.
- `manifest.json` declara `requirements: []`: no añadir dependencias de terceros; `aiohttp` viene con HA.
- El README documenta los endpoints usados y el procedimiento para obtener el token — actualízalo si cambian.
