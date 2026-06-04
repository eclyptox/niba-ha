# Niba para Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?logo=homeassistantcommunitystore&logoColor=white)](https://github.com/hacs/integration)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.1%2B-blue?logo=homeassistant&logoColor=white)](https://www.home-assistant.io/)
[![Tests](https://img.shields.io/badge/tests-6%20passed-brightgreen?logo=pytest&logoColor=white)](tests/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Integración personalizada para Home Assistant que consulta el consumo eléctrico, facturas y saldo de los clientes de [Niba](https://niba.es), la cooperativa de energía renovable.

> **Estado:** en desarrollo activo. El cliente API y los parsers están completos y testeados. El flujo de configuración y los sensores están en construcción.

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

1. Entra en [clientes.niba.es](https://clientes.niba.es) con tu navegador.
2. Abre DevTools (F12) → pestaña **Application → Local Storage** (o **Network** → cualquier petición a `api.clientes.niba.es`).
3. Copia el valor del token. Puedes pegarlo tal cual (`token eyJ...`) o solo la parte JWT (`eyJ...`).

El token se decodifica localmente solo para leer la fecha de expiración y el email. Niba lo valida al llamar a `/users/me`.

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
