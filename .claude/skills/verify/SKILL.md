---
name: verify
description: Quick sanity check — compile-checks key Python files and runs ruff lint. Use after making changes to catch syntax errors and code issues.
---

Run the following checks and report results. Stop at the first failure category and help fix it before continuing.

## Step 1: Syntax check (py_compile)

Run `python -m py_compile <file>` on each of these files:
- `app/__init__.py`
- `app/models.py`
- `app/database.py`
- `app/auth.py`
- All `.py` files in `app/routes/`
- All `.py` files in `pipeline/`

Report any files that fail to compile.

## Step 2: Import check

Run:
```
python -c "import app; from app import models, database"
```

If this fails, diagnose the missing dependency or import error.

## Step 3: Ruff lint (if available)

Run `ruff check .` to catch code quality issues. Focus on errors and warnings — ignore style-only suggestions unless they indicate bugs.

## Summary

After all checks pass, report a one-line summary: "verify passed — N files checked, no issues" or list what needs fixing.
