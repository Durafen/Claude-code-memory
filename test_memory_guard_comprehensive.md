# Memory Guard Comprehensive Test Prompt

Test Memory Guard functionality across all languages and operation types. Execute these tests systematically:

## 1. TRIVIAL OPERATIONS (Should Skip AI Testing)

### Python Trivial
```python
import os
from pathlib import Path
x = 1
API_KEY = "secret"
```

### JavaScript Trivial
```javascript
import React from 'react';
const data = require('./data');
const y = 2;
const CONFIG = "value";
```

### TypeScript Trivial
```typescript
import { Component } from '@angular/core';
const result = api.getData();
const ENDPOINT = "https://api.com";
```

## 2. NON-TRIVIAL (Should Test with AI)

### Python Definitions
```python
def process_data(input_data):
    return input_data.upper()

class DataProcessor:
    def __init__(self):
        pass
```

### JavaScript Definitions
```javascript
function processData(input) {
    return input.toUpperCase();
}

const handler = () => {
    console.log('processing');
};
```

### TypeScript Definitions
```typescript
private filterData(items: Item[]): Item[] {
    return items.filter(item => item.active);
}

public async getData(): Promise<Data[]> {
    return await this.api.fetch();
}
```

## 3. DUPLICATE IMPLEMENTATIONS (Should Block)

Test creating functions that duplicate existing functionality:

```python
# If codebase has existing hash_password function
def hash_password_new(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()
```

```javascript
// If codebase has existing validateEmail function
function checkEmailValid(email) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}
```

## 4. COMPLEX LOGIC (Should Test, Not Block)

```python
if user.is_authenticated():
    for item in cart.items:
        total += item.price * item.quantity

try:
    response = api.call(endpoint)
    data = json.loads(response.text)
except Exception as e:
    logger.error(f"API error: {e}")
```

## Expected Results:

**✅ SKIP (Trivial):** Imports, simple assignments, constants
**🤖 TEST (Non-trivial):** All function/class definitions, complex logic
**❌ BLOCK:** Only duplicate implementations of existing functionality
**✅ ALLOW:** Function calls, using existing code, new non-duplicate logic

Test by editing files in main project directory and checking `memory_guard_debug.txt` for correct categorization.
