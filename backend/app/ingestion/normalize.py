import hashlib
import json
import math
import re
from datetime import datetime
from html import unescape
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup
from pydantic import BaseModel, Field, field_validator

from app.locations import CITY_ALIASES
SKILLS = ["Python", "SQL", "Machine Learning", "JavaScript", "TypeScript", "React", "Vue", "Java", "C++", "C#", "Go", "Rust", "Docker", "Kubernetes", "AWS", "Azure", "GCP", "PostgreSQL", "MySQL", "MongoDB", "TensorFlow", "PyTorch", "Spark", "Airflow", "FastAPI", "Django", "Git", "Linux", "Excel", "Power BI", "Tableau"]
SOURCE_HOSTS = {"104": ("104.com.tw",), "1111": ("1111.com.tw",), "cake": ("cake.me", "cakeresume.com"), "linkedin": ("linkedin.com",), "indeed": ("indeed.com",)}


def plain(value) -> str:
    if not value:
        return ""
    return unescape(BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True))


def city_for(location: str) -> str:
    normalized = location.replace("臺", "台").lower()
    # New Taipei must be checked before Taipei.
    ordered = sorted(CITY_ALIASES.items(), key=lambda pair: pair[0] != "新北市")
    for city, aliases in ordered:
        if any(alias.replace("臺", "台").lower() in normalized for alias in aliases):
            return city
    if "hsinchu" in normalized:
        return "新竹市"
    if "remote" in normalized or "遠端" in normalized:
        return "遠端"
    return "其他"


def number(value) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) and result > 0 else None
    except (TypeError, ValueError):
        return None


def parse_salary(text: str) -> dict:
    """Parse only salary-specific text, never an entire job description."""
    currency = "TWD" if re.search(r"TWD|NT\$|新台幣|月薪|年薪|時薪", text, re.I) else None
    for code in ("USD", "EUR", "JPY", "CNY", "HKD", "SGD"):
        if code in text.upper():
            currency = code
    period = None
    if re.search(r"月|month", text, re.I):
        period = "month"
    elif re.search(r"年|year|annual", text, re.I):
        period = "year"
    elif re.search(r"時|hour", text, re.I):
        period = "hour"
    elif re.search(r"日|day", text, re.I):
        period = "day"
    if re.search(r"面議|negotiable|未提供", text, re.I):
        return dict(salary_text=text, salary_currency=currency, salary_period=period, salary_min=None, salary_max=None)
    values = []
    for match in re.finditer(r"(\d[\d,]*(?:\.\d+)?)\s*([萬万kKmM]?)", text):
        value = float(match.group(1).replace(",", ""))
        value *= {"萬": 10000, "万": 10000, "k": 1000, "m": 1000000}.get(match.group(2).lower(), 1)
        values.append(value)
    return dict(salary_text=text or "薪資未提供", salary_currency=currency, salary_period=period, salary_min=values[0] if values else None, salary_max=values[1] if len(values) > 1 else None)


class NormalizedJob(BaseModel):
    source: str
    external_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    company: str = Field(min_length=1, max_length=500)
    location: str = ""
    location_city: str = "其他"
    url: str
    description: str = ""
    skills: list[str] = Field(default_factory=list)
    salary_text: str = "薪資未提供"
    salary_currency: str | None = None
    salary_period: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    annual_salary_min: int | None = None
    annual_salary_max: int | None = None
    salary_estimated: bool = False
    is_active: bool = True
    source_updated_at: datetime | None = None

    @field_validator("url")
    @classmethod
    def safe_url(cls, value: str, info):
        parts = urlsplit(value)
        allowed = SOURCE_HOSTS.get(info.data.get("source"), ())
        if parts.scheme not in ("http", "https") or not any(parts.hostname == host or (parts.hostname or "").endswith("." + host) for host in allowed):
            raise ValueError("Job URL must belong to its source")
        return urlunsplit(("https", parts.netloc, parts.path, parts.query, ""))

    def finalize(self):
        self.title = plain(self.title)[:500]
        self.company = plain(self.company)[:500]
        self.description = plain(self.description)[:40000]
        self.location = plain(self.location)[:500]
        self.location_city = city_for(self.location)
        if not self.title or not self.company:
            raise ValueError("Missing title or company")
        self.salary_min, self.salary_max = number(self.salary_min), number(self.salary_max)
        self.annual_salary_min = self.annual_salary_max = None
        self.salary_estimated = False
        if self.salary_currency == "TWD" and self.salary_period in ("month", "year"):
            factor = 12 if self.salary_period == "month" else 1
            self.annual_salary_min = int(self.salary_min * factor) if self.salary_min else None
            self.annual_salary_max = int(self.salary_max * factor) if self.salary_max else None
            self.salary_estimated = self.salary_period == "month" and self.salary_min is not None
        blob = f"{self.title} {self.description}"
        detected = [skill for skill in SKILLS if re.search(r"(?<!\w)" + re.escape(skill) + r"(?!\w)", blob, re.I)]
        self.skills = list(dict.fromkeys([plain(x) for x in self.skills if x] + detected))[:20]
        return self

    def storage_values(self) -> dict:
        values = self.model_dump()
        values["search_text"] = " ".join([self.title, self.company, self.description, " ".join(self.skills)]).lower().replace("臺", "台")
        values["content_hash"] = hashlib.sha256(json.dumps(self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        return values
