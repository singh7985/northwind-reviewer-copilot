"""Seed the 5 employees from the case study submissions/ folder."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ..config import get_settings
from ..db import session_scope
from ..models import Employee


log = logging.getLogger(__name__)


def seed_employees() -> int:
    s = get_settings()
    sub_dir = Path(s.submissions_dir)
    seed_file = Path(s.employees_seed)

    employees: list[dict] = []
    if sub_dir.exists():
        for emp_json in sorted(sub_dir.glob("*/employee_info.json")):
            employees.append(json.loads(emp_json.read_text()))
    elif seed_file.exists():
        employees = json.loads(seed_file.read_text())

    if not employees:
        log.warning("No employees found to seed")
        return 0

    # Mirror to data/employees.json for convenience
    seed_file.parent.mkdir(parents=True, exist_ok=True)
    seed_file.write_text(json.dumps(employees, indent=2))

    with session_scope() as db:
        for e in employees:
            row = db.get(Employee, e["employee_id"])
            if row is None:
                row = Employee(id=e["employee_id"])
                db.add(row)
            row.name = e["name"]
            row.grade = int(e["grade"])
            row.title = e["title"]
            row.department = e["department"]
            row.manager_id = e.get("manager_id")
            row.home_base = e["home_base"]
    log.info("Seeded %d employees", len(employees))
    return len(employees)
