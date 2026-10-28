"""Database onboarding: staged connection tests (NDJSON) and bulk registration.

Staged test stages, in order: validate → driver → dns → tcp → tls → authenticate → version →
privileges → capabilities, followed by a ``result`` event. Each stage reports ``running`` and
then a terminal status; the first ``fail`` stops the run and the remaining stages are reported
``skipped``. DNS and TCP are checked by DBManager itself; TLS and authentication are negotiated
by the engine driver (TLS precedes authentication in the PostgreSQL, MySQL, TDS, Oracle Net and
DRDA protocols), so their outcome is derived from the driver's normalized error class.
"""

from __future__ import annotations

import asyncio
import csv
import io
import re
import socket
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import (
    AdapterAuthenticationError,
    AdapterDatabaseNotFound,
    AdapterError,
    AdapterPermissionError,
    AdapterTLSError,
    BaseDatabaseAdapter,
    CapabilityLevel,
)
from app.adapters.registry import adapter_class_for, normalize_engine, supported_engines
from app.models import Application, Cluster, Environment, Organization, Server, Site

STAGES = ("validate", "driver", "dns", "tcp", "tls", "authenticate", "version", "privileges", "capabilities")

AdapterFactory = Callable[[], Awaitable[BaseDatabaseAdapter]]


def _event(stage: str, status: str, message: str = "", duration_ms: float | None = None, detail: Any = None) -> dict:
    return {"stage": stage, "status": status, "message": message, "duration_ms": duration_ms, "detail": detail}


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)


async def staged_test(
    *,
    engine: str,
    host: str,
    port: int,
    tls_mode: str,
    make_adapter: AdapterFactory | None,
    validation_error: str | None = None,
    simulated: bool = False,
    connect_timeout: float = 8.0,
) -> AsyncIterator[dict[str, Any]]:
    """Yield stage events; ``make_adapter`` builds the adapter (credentials resolved by the caller)."""
    result: dict[str, Any] = {"ok": False, "engine": engine, "message": "", "error_code": None}
    remaining = list(STAGES)

    def skip_rest(reason: str) -> list[dict[str, Any]]:
        return [_event(s, "skipped", reason) for s in remaining]

    def done(stage: str) -> None:
        remaining.remove(stage)

    async def fail(stage: str, msg: str, code: str, t0: float, detail: Any = None) -> AsyncIterator[dict[str, Any]]:
        done(stage)
        yield _event(stage, "fail", msg, _ms(t0), detail)
        for e in skip_rest(f"skipped: {stage} failed"):
            yield e
        result.update(message=msg, error_code=code)
        yield {"stage": "result", "status": "fail", "result": result}

    # ---- validate ------------------------------------------------------------------------
    t0 = time.perf_counter()
    yield _event("validate", "running", "checking the connection parameters")
    if validation_error is None:
        if engine not in supported_engines():
            validation_error = f"unsupported engine {engine!r}"
        elif not adapter_class_for(engine).implemented:
            validation_error = f"no {adapter_class_for(engine).display_name} adapter is available yet"
    if validation_error:
        async for e in fail("validate", validation_error, "VALIDATION_FAILED", t0):
            yield e
        return
    done("validate")
    yield _event("validate", "ok", f"{adapter_class_for(engine).display_name} {host}:{port}", _ms(t0))

    # ---- driver --------------------------------------------------------------------------
    t0 = time.perf_counter()
    yield _event("driver", "running", "checking the database driver in this backend")
    from app.adapters import drivers

    row = None if simulated else drivers.engine_driver(engine)
    if row is not None and row["status"] == "missing":
        msg = row["remediation"] or row["error"] or "driver missing"
        async for e in fail("driver", msg, "DRIVER_NOT_INSTALLED", t0, {"driver": row["driver"]}):
            yield e
        return
    done("driver")
    if row is None:
        yield _event("driver", "ok", "simulated server: no driver needed" if simulated else "no driver needed", _ms(t0))
    elif row["status"] == "degraded":
        yield _event(
            "driver", "warn", row["error"] or "degraded", _ms(t0), {"driver": row["driver"], "version": row["version"]}
        )
    else:
        yield _event(
            "driver", "ok", f"{row['driver']} {row['version'] or ''}".strip(), _ms(t0), {"driver": row["driver"]}
        )

    # ---- dns / tcp -----------------------------------------------------------------------
    addresses: list[str] = []
    if simulated:
        for s in ("dns", "tcp", "tls"):
            done(s)
            yield _event(s, "skipped", "simulated server: no network connection is made")
    else:
        t0 = time.perf_counter()
        yield _event("dns", "running", f"resolving {host}")
        try:
            infos = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM), timeout=connect_timeout
            )
            addresses = sorted({str(i[4][0]) for i in infos})
        except (TimeoutError, OSError) as exc:
            async for e in fail("dns", f"cannot resolve {host}: {exc}", "CONNECTION_FAILED", t0):
                yield e
            return
        done("dns")
        yield _event("dns", "ok", ", ".join(addresses[:4]), _ms(t0), {"addresses": addresses})

        t0 = time.perf_counter()
        yield _event("tcp", "running", f"opening a TCP connection to {host}:{port}")
        try:
            _r, w = await asyncio.wait_for(asyncio.open_connection(addresses[0], port), timeout=connect_timeout)
            w.close()
            try:
                await w.wait_closed()
            except OSError:
                pass
        except TimeoutError:
            async for e in fail(
                "tcp",
                f"no answer from {host}:{port} within {connect_timeout:g}s (firewall or wrong port?)",
                "CONNECTION_FAILED",
                t0,
            ):
                yield e
            return
        except OSError as exc:
            async for e in fail(
                "tcp", f"{host}:{port} refused or unreachable: {exc.strerror or exc}", "CONNECTION_FAILED", t0
            ):
                yield e
            return
        done("tcp")
        yield _event("tcp", "ok", f"port {port} is open", _ms(t0), {"address": addresses[0]})

        t0 = time.perf_counter()
        yield _event("tls", "running", f"TLS mode {tls_mode}: negotiated by the driver")

    # ---- driver connection: TLS + authentication ----------------------------------------
    assert make_adapter is not None
    adapter: BaseDatabaseAdapter | None = None
    t_auth = time.perf_counter()
    try:
        adapter = await make_adapter()
        info = await adapter.test_connection()
        latency = _ms(t_auth)
        if not simulated:
            done("tls")
            if tls_mode == "disable":
                yield _event("tls", "warn", "TLS disabled by configuration: traffic is not encrypted", _ms(t0))
            elif tls_mode == "prefer":
                yield _event(
                    "tls",
                    "warn",
                    "TLS preferred: the driver falls back to plaintext if the server offers no TLS",
                    _ms(t0),
                )
            else:
                verified = tls_mode in ("verify-ca", "verify-full")
                yield _event(
                    "tls",
                    "ok",
                    f"encrypted ({tls_mode}{', certificate verified' if verified else ', certificate not verified'})",
                    _ms(t0),
                )
        done("authenticate")
        yield _event("authenticate", "running", "logging in")
        yield _event("authenticate", "ok", f"authenticated as {info.current_user or 'the configured user'}", latency)

        t1 = time.perf_counter()
        done("version")
        yield _event("version", "running", "reading server version")
        eos = (info.extra or {}).get("end_of_support")
        if eos:
            yield _event(
                "version", "warn", f"{info.version} — vendor support ended {eos}", _ms(t1), {"version": info.version}
            )
        else:
            yield _event("version", "ok", info.edition or info.version, _ms(t1), {"version": info.version})

        t1 = time.perf_counter()
        yield _event("privileges", "running", "checking what the account may see")
        caps = await adapter.discover_capabilities()
        need_priv = sorted(c.value for c, v in caps.items() if v.level is CapabilityLevel.REQUIRES_PRIVILEGE)
        done("privileges")
        if need_priv:
            notes = {c.value: caps[c].note for c in caps if c.value in need_priv}
            yield _event(
                "privileges",
                "warn",
                f"{len(need_priv)} feature(s) need more privileges: {', '.join(need_priv[:6])}",
                _ms(t1),
                {"requires_privilege": notes},
            )
        else:
            yield _event("privileges", "ok", "the account has every privilege KBM DBManager uses", _ms(t1))

        t1 = time.perf_counter()
        yield _event("capabilities", "running", "summarising capabilities")
        by_level: dict[str, int] = {}
        for v in caps.values():
            by_level[v.level.value] = by_level.get(v.level.value, 0) + 1
        usable = sum(1 for v in caps.values() if v.level.usable)
        done("capabilities")
        yield _event(
            "capabilities", "ok", f"{usable} of {len(caps)} capabilities usable", _ms(t1), {"by_level": by_level}
        )
        result.update(
            ok=True,
            message="Connected successfully",
            version=info.version,
            latency_ms=latency,
            server_info=info.model_dump(mode="json"),
            capabilities={k.value: v.model_dump(mode="json") for k, v in caps.items()},
        )
        yield {"stage": "result", "status": "ok", "result": result}
    except AdapterError as exc:
        msg, code = str(exc), exc.code.value
        if isinstance(exc, AdapterTLSError) and "tls" in remaining:
            async for e in fail("tls", msg, code, t0):
                yield e
            return
        if "tls" in remaining:
            done("tls")
            yield _event(
                "tls",
                "ok" if tls_mode not in ("disable", "prefer") else "warn",
                f"handshake completed ({tls_mode})",
                _ms(t0),
            )
        stage = next(s for s in ("authenticate", "version", "privileges", "capabilities") if s in remaining)
        if stage == "authenticate":
            yield _event("authenticate", "running", "logging in")
        hint = ""
        if isinstance(exc, AdapterAuthenticationError):
            hint = " — check the user name and password"
        elif isinstance(exc, AdapterDatabaseNotFound):
            hint = " — check the database / service name"
        elif isinstance(exc, AdapterPermissionError):
            hint = " — grant the monitoring privileges listed in the permissions guide"
        async for e in fail(stage, msg + hint, code, t_auth):
            yield e
    finally:
        if adapter is not None:
            await adapter.close()


# ======================================================================================
# bulk onboarding
# ======================================================================================
TEMPLATE_COLUMNS = [
    "name",
    "engine",
    "host",
    "port",
    "environment",
    "site",
    "cluster",
    "application",
    "default_database",
    "tls_mode",
    "management_mode",
    "credential",
    "tags",
    "description",
]
SECRET_KEY = re.compile(r"pass(word|wd)?|pwd|secret|token|(^|_)key$", re.I)
SECRET_MSG = "Never put passwords in bulk files: use a shared credential (credential column)"  # noqa: S105
TLS_MODES = {"disable", "prefer", "require", "verify-ca", "verify-full"}
MODES = {
    "monitoring": "monitoring",
    "monitoring_only": "monitoring",
    "administration": "administration",
    "administration_enabled": "administration",
}
CRED_RE = re.compile(r"^(server|env|file|vault):(.+)$")


def template_csv() -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(TEMPLATE_COLUMNS)
    w.writerow(
        [
            "pg-prod-01",
            "postgresql",
            "pg-prod-01.example.internal",
            "5432",
            "Production",
            "DC1",
            "",
            "billing",
            "postgres",
            "verify-full",
            "monitoring",
            "server:pg-prod-02",
            "team=billing;tier=gold",
            "Billing primary",
        ]
    )
    w.writerow(
        [
            "ora-uat-01",
            "oracle",
            "10.0.12.40",
            "1521",
            "UAT",
            "",
            "",
            "",
            "ORCLPDB1",
            "require",
            "monitoring",
            "env:ORA_UAT_MONITOR_PASSWORD@dbm_monitor",
            "",
            "",
        ]
    )
    return buf.getvalue()


def _err(field: str, message: str) -> dict[str, str]:
    return {"field": field, "message": message}


async def _lookup(db: AsyncSession, model: Any) -> dict[str, Any]:
    return {str(o.name).lower(): o for o in (await db.execute(select(model))).scalars()}


async def validate_rows(db: AsyncSession, rows: list[dict[str, Any]]) -> dict[str, Any]:
    from app.schemas.inventory import ServerCreate

    envs, sites = await _lookup(db, Environment), await _lookup(db, Site)
    clusters, apps = await _lookup(db, Cluster), await _lookup(db, Application)
    existing_names = {str(n).lower() for (n,) in (await db.execute(select(Server.name))).all()}
    existing_eps = {
        (e, str(h).lower(), int(p))
        for e, h, p in (await db.execute(select(Server.engine, Server.host, Server.port))).all()
    }
    server_by_name = {str(s.name).lower(): s for s in (await db.execute(select(Server))).scalars()}
    seen_names: dict[str, int] = {}
    seen_eps: dict[tuple[str, str, int], int] = {}
    out = []
    for idx, raw in enumerate(rows):
        errors: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        r = {str(k).strip().lower(): (v.strip() if isinstance(v, str) else v) for k, v in (raw or {}).items()}
        for k in list(r):
            if SECRET_KEY.search(k) and r[k] not in (None, ""):
                errors.append(_err(k, SECRET_MSG))
                r.pop(k)
            elif SECRET_KEY.search(k):
                r.pop(k)
        unknown = [k for k in r if k not in TEMPLATE_COLUMNS]
        for k in unknown:
            warnings.append(_err(k, "unknown column ignored"))
        norm: dict[str, Any] = {}
        name = str(r.get("name") or "")
        engine_raw = str(r.get("engine") or "")
        try:
            engine = normalize_engine(engine_raw) if engine_raw else ""
        except ValueError:
            engine = engine_raw.lower()
        if not engine:
            errors.append(_err("engine", "engine is required"))
        elif engine not in supported_engines() or engine == "demo":
            errors.append(_err("engine", f"unsupported engine {engine_raw!r}"))
        port_raw = r.get("port")
        port: int | None = None
        if port_raw in (None, ""):
            if engine in supported_engines():
                port = adapter_class_for(engine).default_port
                warnings.append(_err("port", f"defaulted to {port}"))
            else:
                errors.append(_err("port", "port is required"))
        else:
            try:
                port = int(str(port_raw))
            except ValueError:
                errors.append(_err("port", "port must be a number"))
        tls = str(r.get("tls_mode") or "verify-full").lower()
        if tls not in TLS_MODES:
            errors.append(_err("tls_mode", f"tls_mode must be one of {', '.join(sorted(TLS_MODES))}"))
        elif tls in ("disable", "prefer"):
            warnings.append(_err("tls_mode", f"tls_mode {tls}: the connection may not be encrypted"))
        mode_raw = str(r.get("management_mode") or "monitoring").lower().replace(" ", "_")
        mode = MODES.get(mode_raw)
        if mode is None:
            errors.append(_err("management_mode", "management_mode must be monitoring or administration"))
        elif mode == "administration":
            warnings.append(_err("management_mode", "administration enabled: change actions will be offered"))
        tags = []
        for part in [t for t in str(r.get("tags") or "").split(";") if t.strip()]:
            k, _, v = part.partition("=")
            tags.append({"key": k.strip(), "value": v.strip()})
        # references
        env_name = str(r.get("environment") or "")
        env = envs.get(env_name.lower()) if env_name else None
        if not env_name:
            errors.append(_err("environment", "environment is required"))
        elif env is None:
            errors.append(_err("environment", f"unknown environment {env_name!r} (create it first)"))
        refs: dict[str, Any] = {}
        for col, table in (("site", sites), ("cluster", clusters), ("application", apps)):
            val = str(r.get(col) or "")
            if val:
                obj = table.get(val.lower())
                if obj is None:
                    warnings.append(_err(col, f"{col} {val!r} does not exist and will be created"))
                refs[col] = {"name": val, "id": str(obj.id) if obj else None}
        cred = str(r.get("credential") or "")
        credential: dict[str, Any] | None = None
        if cred:
            m = CRED_RE.match(cred)
            if not m:
                errors.append(
                    _err(
                        "credential",
                        "credential must be server:<registered server>, env:<VAR>@<user>, file:<path>@<user> "
                        "or vault:<[mount:]path#key>@<user>",
                    )
                )
            else:
                kind, ref = m.group(1), m.group(2)
                if kind == "server":
                    src = server_by_name.get(ref.lower())
                    if src is None:
                        errors.append(
                            _err("credential", f"no registered server named {ref!r} to copy the credential from")
                        )
                    else:
                        credential = {"kind": "server", "server": src.name}
                else:
                    secret_ref, _, user = ref.rpartition("@")
                    if not secret_ref or not user:
                        errors.append(_err("credential", f"{kind}: references need the user name: {kind}:<ref>@<user>"))
                    else:
                        credential = {"kind": kind, "secret_ref": secret_ref, "username": user}
        else:
            warnings.append(_err("credential", "no credential: the server is registered without monitoring"))
        # schema-level checks (name pattern, host syntax, port range)
        if engine and port is not None and name:
            try:
                ServerCreate(
                    name=name,
                    engine=engine,
                    host=str(r.get("host") or ""),
                    port=port,
                    tls_mode=tls,  # type: ignore[arg-type]
                    management_mode=mode or "monitoring",
                    tags=tags,  # type: ignore[arg-type]
                    default_database=r.get("default_database") or None,
                    description=r.get("description") or None,
                )
            except ValidationError as exc:
                for e in exc.errors():
                    errors.append(
                        _err(str(e["loc"][0]) if e["loc"] else "row", str(e["msg"]).removeprefix("Value error, "))
                    )
        elif not name:
            errors.append(_err("name", "name is required"))
        if not r.get("host"):
            errors.append(_err("host", "host is required"))
        # duplicates
        lname = name.lower()
        if lname:
            if lname in existing_names:
                errors.append(_err("name", f"a server named {name!r} is already registered"))
            elif lname in seen_names:
                errors.append(_err("name", f"duplicate of row {seen_names[lname] + 1}"))
            seen_names.setdefault(lname, idx)
        if engine and r.get("host") and port is not None:
            ep = (engine, str(r["host"]).lower(), port)
            if ep in existing_eps:
                errors.append(_err("host", f"{r['host']}:{port} ({engine}) is already registered"))
            elif ep in seen_eps:
                errors.append(_err("host", f"same endpoint as row {seen_eps[ep] + 1}"))
            seen_eps.setdefault(ep, idx)
        norm = {
            "name": name,
            "engine": engine,
            "host": r.get("host") or "",
            "port": port,
            "environment": {"name": env.name, "id": str(env.id)} if env else None,
            **refs,
            "default_database": r.get("default_database") or None,
            "tls_mode": tls,
            "management_mode": mode or "monitoring",
            "credential": credential,
            "tags": tags,
            "description": r.get("description") or None,
        }
        out.append({"index": idx, "ok": not errors, "errors": errors, "warnings": warnings, "normalized": norm})
    return {
        "rows": out,
        "summary": {
            "total": len(out),
            "valid": sum(1 for r in out if r["ok"]),
            "invalid": sum(1 for r in out if not r["ok"]),
            "warnings": sum(1 for r in out if r["warnings"]),
        },
    }


async def _get_or_create(db: AsyncSession, model: Any, name: str, **extra: Any) -> Any:
    obj = (await db.execute(select(model).where(func.lower(model.name) == name.lower()))).scalar_one_or_none()
    if obj is None:
        org = (await db.execute(select(Organization).order_by(Organization.created_at).limit(1))).scalar_one()
        obj = model(organization_id=org.id, name=name, **extra)
        db.add(obj)
        await db.flush()
    return obj


async def create_rows(
    db: AsyncSession, rows: list[dict[str, Any]], *, monitoring_enabled: bool, actor: str
) -> tuple[list[dict[str, Any]], list[Server]]:
    from app.schemas.inventory import CredentialIn, ServerCreate
    from app.services import inventory, secrets

    checked = await validate_rows(db, rows)
    results: list[dict[str, Any]] = []
    created: list[Server] = []
    for row in checked["rows"]:
        n = row["normalized"]
        if not row["ok"]:
            results.append(
                {
                    "index": row["index"],
                    "ok": False,
                    "name": n["name"],
                    "error": "; ".join(f"{e['field']}: {e['message']}" for e in row["errors"]),
                }
            )
            continue
        try:
            async with db.begin_nested():
                ids: dict[str, Any] = {"environment_id": n["environment"]["id"]}
                for col, model in (("site", Site), ("cluster", Cluster), ("application", Application)):
                    if n.get(col):
                        ids[f"{col}_id"] = (await _get_or_create(db, model, n[col]["name"])).id
                cred = n["credential"]
                mon: CredentialIn | None = None
                copy_from: Server | None = None
                if cred and cred["kind"] in ("env", "file", "vault"):
                    mon = CredentialIn(username=cred["username"], provider=cred["kind"], secret_ref=cred["secret_ref"])
                body = ServerCreate(
                    name=n["name"],
                    engine=n["engine"],
                    host=n["host"],
                    port=n["port"],
                    default_database=n["default_database"],
                    tls_mode=n["tls_mode"],
                    management_mode=n["management_mode"],
                    tags=n["tags"],
                    description=n["description"],
                    monitoring_enabled=monitoring_enabled and cred is not None,
                    monitoring_credential=mon,
                    **ids,
                )
                s = await inventory.create_server(db, body, actor)
                if cred and cred["kind"] == "server":
                    copy_from = (await db.execute(select(Server).where(Server.name == cred["server"]))).scalar_one()
                    src = await secrets.get_credential(db, copy_from.id, "monitoring")
                    if src is None:
                        raise ValueError(f"server {copy_from.name} has no monitoring credential to copy")
                    await secrets.set_credential(
                        db,
                        s,
                        "monitoring",
                        username=src.username,
                        password=secrets.resolve_password(src) if src.provider == "local" else None,
                        provider=src.provider,
                        secret_ref=src.secret_ref,
                        actor=actor,
                    )
            created.append(s)
            results.append({"index": row["index"], "ok": True, "server_id": str(s.id), "name": s.name})
        except Exception as exc:  # noqa: BLE001 - one bad row never aborts the batch
            msg = getattr(exc, "message", None) or str(exc)
            results.append({"index": row["index"], "ok": False, "name": n["name"], "error": msg[:500]})
    return results, created
