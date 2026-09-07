# Niba para Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?logo=homeassistantcommunitystore&logoColor=white)](https://github.com/hacs/integration)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.11%2B-blue?logo=homeassistant&logoColor=white)](https://www.home-assistant.io/)
[![Tests](https://img.shields.io/badge/tests-85%20passed-brightgreen?logo=pytest&logoColor=white)](tests/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Integración personalizada para Home Assistant que consulta el consumo eléctrico, facturas y saldo de los clientes de [Niba](https://niba.es), la cooperativa de energía renovable.

> **Requiere Home Assistant 2024.11 o posterior.**

## Entidades

| Sensor | Descripción |
| --- | --- |
| Consumo período actual | kWh del período en curso, con la fecha de inicio, la última lectura y los días transcurridos como atributos |
| Importe período actual | Importe acumulado del período en curso |
| Importe estimado fin de período | Estimación de Niba del importe al cierre del período |
| Consumo estimado fin de período | Estimación de Niba de los kWh al cierre del período |
| Consumo medio diario | kWh por día del período en curso |
| Importe medio diario | Coste por día del período en curso |
| Comparación período anterior | Variación porcentual frente al período anterior |
| Saldo monedero | Saldo disponible, con lo pendiente, cargado y gastado como atributos |
| Batería solar | Parte del saldo que proviene de los excedentes de autoconsumo |
| Última factura | Importe de la última factura, con el desglose completo como atributos: período, estado, consumo, base imponible, impuestos, término de potencia y de energía, alquiler del contador y excedentes de autoconsumo |
| Consumo acumulado | Total histórico en kWh, apto para el Energy Dashboard |
| Caducidad del token | *(diagnóstico)* Fecha de expiración del JWT, para avisarte por automatización antes de que caduque |

Cuando Niba emite una factura nueva, la integración crea una notificación persistente y dispara el evento `niba_new_bill` (con `billing_code`, `period`, `total_amount` y `status`), que puedes usar como disparador de automatizaciones.

Si tienes varios puntos de suministro en la misma cuenta, añade la integración una vez por cada CUPS.

## Instalación con HACS (recomendado)

1. En Home Assistant ve a **HACS → Integraciones → ⋮ → Repositorios personalizados**.
2. Añade `https://github.com/eclyptox/niba-ha` con categoría **Integración**.
3. Busca **Niba** en HACS e instala.
4. Reinicia Home Assistant.

## Instalación manual

1. Copia `custom_components/niba` dentro de la carpeta `custom_components` de tu configuración de Home Assistant.
2. Reinicia Home Assistant.

## Autenticación

Niba no expone una API pública. La integración usa el JWT que genera la web de clientes:

1. Entra en [clientes.niba.es](https://clientes.niba.es) e inicia sesión.
2. Pulsa **F12** para abrir las herramientas de desarrollo del navegador.
3. Ve a **Application → Local Storage → `https://clientes.niba.es`** (en Firefox la pestaña se llama **Almacenamiento**).
4. Copia el valor de la clave **`token`**: un texto largo que empieza por `eyJ`.

Puedes pegarlo tal cual (`token eyJ...`) o solo la parte JWT (`eyJ...`).

> Como alternativa, en la pestaña **Network** abre cualquier petición a `api.clientes.niba.es` y copia el valor de la cabecera `authorization` (sin el prefijo `token `, o con él, da igual).

El token se decodifica localmente solo para leer la fecha de expiración y el email. Niba lo valida al llamar a `/users/me`.

### Cuando el token caduca

Los tokens de Niba duran **90 días** desde que inicias sesión. Cuando ocurre, la integración lo detecta y Home Assistant muestra el aviso **«Se requiere volver a autenticar»** en *Ajustes → Dispositivos y servicios*. Pulsa **Volver a autenticar** y repite los pasos de arriba; el diálogo los explica también.

Si la clave `token` no aparece en el navegador, cierra sesión en la web de Niba y vuelve a entrar para que se genere una nueva.

## Endpoints utilizados

| Endpoint | Descripción |
| --- | --- |
| `GET /users/me` | Datos del titular de la cuenta |
| `GET /cups/{cups}/bills` | Historial de facturas |
| `GET /cups/{cups}/consumption-period` | Consumo del período de facturación actual |
| `GET /balances` | Saldo del monedero y batería solar |

## Notas técnicas

- Usa `aiohttp` a través del `DataUpdateCoordinator` de Home Assistant.
- Las cuatro peticiones de cada ciclo se lanzan en paralelo con `asyncio.gather`.
- El token JWT se valida localmente (expiración) antes de cada ciclo; si ha expirado, la entrada de configuración pasa a estado de error para que el usuario lo renueve.
- Intervalo de actualización: consumo cada 60 minutos, facturas cada 6 horas.
- El CUPS se toma de `/contracts`, que es lo que hace la propia web de Niba, y se normaliza a sus primeros 20 caracteres: es la forma en que la API los identifica (sus respuestas devuelven `cups_electricity` con esa longitud). Si Niba deja de reconocer el CUPS guardado, la integración vuelve a consultar el contrato y se corrige sola.
- `end_at` del período es la última fecha con datos, no la fecha de cierre de la factura, así que no se expone ningún "días restantes".
- Las medias diarias usan los valores que envía Niba; si no llegan, se calculan dividiendo el consumo del período entre los días transcurridos (el mismo criterio que la web).
- El sensor de consumo acumulado nunca decrece. El total en bruto baja durante los días que van desde que Niba cierra un período hasta que emite la factura correspondiente, y Home Assistant leería esa bajada como un reinicio de contador en el Energy Dashboard.

### Sobre el saldo y la batería solar

Es normal que **Batería solar** y **Saldo monedero** coincidan: Niba manda los dos campos por separado (`amount` y `solar_battery`) y `solar_battery` es la parte del saldo que proviene de los excedentes de autoconsumo. Si todo tu saldo se ha generado así y no has gastado nada, los dos valores son iguales.

Para ver los importes en bruto, activa el log de depuración y busca `Raw /balances payload`:

```yaml
logger:
  default: warning
  logs:
    custom_components.niba: debug
```

## Desarrollo

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/ruff check custom_components tests
```
