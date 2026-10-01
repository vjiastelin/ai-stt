"""E-mail routing rules loaded from a TOML file (EMAIL_ROUTING_FILE).

    default = "time017@aeroclub.team"      # optional: replaces EMAIL_TO as the fallback

    [[file_route]]                          # optional, checked first, after EMAIL_ROUTES
    pattern = "AWAD_IVRrecord_*"            # glob on the recording's file name
    to = "anywayanyday-info-gate@yandex.ru"

    [[route]]                               # by the client recognized in the summary
    to = "time001@aeroclub.team"            # a string or a list of addresses
    domains = ["bayer.ru", "bayer.com"]     # the client's e-mail domain (subdomains match)
    companies = ["Байер", "Bayer"]          # fallback: the «Компания:» line

A domain or company listed in two routes is a ConfigError, so a large table
can't silently send a client to two teams.
"""
import re
import tomllib
from dataclasses import dataclass



class RoutingError(ValueError):
    """Invalid routing file; config.load_config re-raises it as ConfigError."""

_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
_LEGAL_FORMS = {"ооо", "оао", "зао", "пао", "ао", "ип", "llc", "ltd", "inc", "gmbh", "ag", "sa", "plc"}
# Cyrillic letters that look like Latin ones: «Х5» and «X5» must be the same company
_HOMOGLYPHS = str.maketrans("авекмнорстух", "abekmhopctyx")


@dataclass(frozen=True)
class ClientRoute:
    to: tuple[str, ...]
    domains: tuple[str, ...]
    companies: tuple[str, ...]  # normalized, see normalize_company()


@dataclass(frozen=True)
class Routing:
    file_routes: tuple[tuple[str, tuple[str, ...]], ...] = ()
    client_routes: tuple[ClientRoute, ...] = ()
    default: tuple[str, ...] = ()


def normalize_company(name: str) -> str:
    """Case/quote/punctuation/legal-form/homoglyph-insensitive company key."""
    text = name.lower().replace("ё", "е")
    text = re.sub(r"[«»\"'`“”„]", " ", text)
    text = re.sub(r"[^\w+&]+", " ", text)
    tokens = [t for t in text.split() if t not in _LEGAL_FORMS]
    return " ".join(tokens).translate(_HOMOGLYPHS)


def _addresses(value, where: str) -> tuple[str, ...]:
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not items:
        raise RoutingError(f"{where}: `to` must be an address or a non-empty list of addresses")
    addresses = tuple(str(a).strip() for a in items)
    for a in addresses:
        if "@" not in a or " " in a or a.startswith("@") or a.endswith("@"):
            raise RoutingError(f"{where}: invalid address {a!r}")
    return addresses


def _strings(table: dict, key: str, where: str) -> list[str]:
    value = table.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise RoutingError(f"{where}: `{key}` must be a list of strings")
    return [v.strip() for v in value if v.strip()]


def parse_routing(data: dict, source: str = "EMAIL_ROUTING_FILE") -> Routing:
    unknown = set(data) - {"default", "file_route", "route"}
    if unknown:
        raise RoutingError(f"{source}: unknown key(s) {', '.join(sorted(unknown))}")
    default = _addresses(data["default"], f"{source}: default") if "default" in data else ()

    file_routes = []
    for i, table in enumerate(data.get("file_route", []), 1):
        where = f"{source}: file_route #{i}"
        if set(table) - {"pattern", "to"} or not str(table.get("pattern", "")).strip():
            raise RoutingError(f"{where} needs exactly `pattern` and `to`")
        file_routes.append((table["pattern"].strip(), _addresses(table.get("to"), where)))

    client_routes, seen_domains, seen_companies = [], {}, {}
    for i, table in enumerate(data.get("route", []), 1):
        where = f"{source}: route #{i}"
        unknown = set(table) - {"to", "domains", "companies", "name"}
        if unknown:
            raise RoutingError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
        to = _addresses(table.get("to"), where)
        domains = []
        for raw in _strings(table, "domains", where):
            domain = raw.lower().lstrip("@")
            if not _DOMAIN_RE.match(domain):
                raise RoutingError(f"{where}: {raw!r} is not a domain")
            if domain in seen_domains and seen_domains[domain] != i:
                raise RoutingError(f"{where}: domain {domain!r} is already in route #{seen_domains[domain]}")
            seen_domains[domain] = i
            domains.append(domain)
        companies = []
        for raw in _strings(table, "companies", where):
            key = normalize_company(raw)
            if not key:
                raise RoutingError(f"{where}: company {raw!r} is empty after normalization")
            if key in seen_companies and seen_companies[key] != i:
                raise RoutingError(f"{where}: company {raw!r} is already in route #{seen_companies[key]}")
            seen_companies[key] = i
            companies.append(key)
        if not domains and not companies:
            raise RoutingError(f"{where} needs `domains` and/or `companies`")
        client_routes.append(ClientRoute(to, tuple(dict.fromkeys(domains)), tuple(dict.fromkeys(companies))))
    return Routing(tuple(file_routes), tuple(client_routes), default)


def load_routing_file(path: str) -> Routing:
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except OSError as exc:
        raise RoutingError(f"EMAIL_ROUTING_FILE {path!r} cannot be read: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise RoutingError(f"EMAIL_ROUTING_FILE {path!r} is not valid TOML: {exc}") from exc
    return parse_routing(data, f"EMAIL_ROUTING_FILE {path!r}")


_EMAIL = re.compile(r"[\w.+-]+@([a-z0-9-]+(?:\.[a-z0-9-]+)+)", re.IGNORECASE)


def _field(summary: str, label: str) -> str | None:
    """Value of a «Label: …» line of the summary, None when the line is absent."""
    prefix = label.lower() + ":"
    for line in summary.splitlines():
        if line.strip().lower().startswith(prefix):
            return line.split(":", 1)[1].strip()
    return None


def client_email_domain(summary: str) -> str:
    """Domain of the client's address: the «Почта:» line, else the first address in the text."""
    line = _field(summary, "Почта")
    text = summary if line is None else line  # «Почта: не указано» → no address, don't look further
    match = _EMAIL.search(text)
    return match.group(1).lower() if match else ""


_NOT_GIVEN = normalize_company("не указано")


def client_company(summary: str) -> str:
    value = _field(summary, "Компания") or ""
    return "" if normalize_company(value) in ("", _NOT_GIVEN) else value


def route_for_client(routing: Routing, summary: str) -> ClientRoute | None:
    """Most specific domain match first, then a company-name match."""
    if not summary or not routing.client_routes:
        return None
    domain = client_email_domain(summary)
    if domain:
        best, best_len = None, 0
        for route in routing.client_routes:
            for d in route.domains:
                if (domain == d or domain.endswith("." + d)) and len(d) > best_len:
                    best, best_len = route, len(d)
        if best is not None:
            return best
    company = normalize_company(client_company(summary))
    if company:
        # whole-word match; the longest alias wins («Дельта Лизинг» over «Дельта»)
        padded = f" {company} "
        best, best_len = None, 0
        for route in routing.client_routes:
            for c in route.companies:
                if f" {c} " in padded and len(c) > best_len:
                    best, best_len = route, len(c)
        return best
    return None
