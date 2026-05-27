from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Employee
from ..schemas import EmployeeIn, EmployeeOut


router = APIRouter(prefix="/employees", tags=["employees"])


@router.get("", response_model=list[EmployeeOut])
def list_employees(db: Session = Depends(get_db)):
    return db.query(Employee).order_by(Employee.name).all()


@router.post("", response_model=EmployeeOut)
def create_employee(payload: EmployeeIn, db: Session = Depends(get_db)):
    if db.get(Employee, payload.id):
        raise HTTPException(409, "Employee already exists")
    emp = Employee(
        id=payload.id,
        name=payload.name,
        grade=payload.grade,
        title=payload.title,
        department=payload.department,
        manager_id=payload.manager_id,
        home_base=payload.home_base,
    )
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


@router.get("/{employee_id}", response_model=EmployeeOut)
def get_employee(employee_id: str, db: Session = Depends(get_db)):
    emp = db.get(Employee, employee_id)
    if not emp:
        raise HTTPException(404, "Employee not found")
    return emp
