# Adaptadores de contrato por linguagem

O contrato (`create_user.schema.json`) é único; cada linguagem só o implementa com suas ferramentas.

> **Status:** exemplos **não executados** (ilustrativos). Só o exemplo Python é validado por teste (`tests/test_contracts.py`).

## Python
```python
class InvalidEmail(ValueError): ...
class WeakPassword(ValueError): ...

def create_user(email: str, pwd: str) -> dict:
    if "@" not in email: raise InvalidEmail(email)
    if len(pwd) < 8: raise WeakPassword()
    return {"id": 1, "status": "active"}
```

## PHP
```php
function createUser(string $email, string $pwd): array {
    if (!str_contains($email, '@')) throw new InvalidArgumentException('InvalidEmail');
    if (strlen($pwd) < 8) throw new InvalidArgumentException('WeakPassword');
    return ['id' => 1, 'status' => 'active'];
}
```

## JavaScript
```js
export function createUser({ email, pwd }) {
  if (!email.includes('@')) throw new Error('InvalidEmail');
  if (pwd.length < 8) throw new Error('WeakPassword');
  return { id: 1, status: 'active' };
}
```
