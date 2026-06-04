# Niba Home Assistant Custom Integration

Custom Home Assistant integration for Niba electricity customers.

This repository is in early development. The first implemented layer is the
Niba API client and parsers for confirmed endpoints:

- `GET /users/me`
- `GET /cups/{cups}/bills`
- `GET /cups/{cups}/consumption-period`
- `GET /balances`

Authentication uses the browser-copied Niba JWT with the required header format:

```http
authorization: token <JWT>
```

The JWT is decoded locally only to read metadata such as `exp` and `email`.
Niba validates the token through `/users/me`.

## Tests

```bash
pytest -q
```
