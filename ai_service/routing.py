"""E-mail routing rules loaded from a TOML file (EMAIL_ROUTING_FILE).

    default = "time017@aeroclub.team"      # optional: replaces EMAIL_TO as the fallback

    [[file_route]]                          # optional, checked first, after EMAIL_ROUTES
    name = "Anywayanyday"                   # optional label for the routing stats
    pattern = "AWAD_IVRrecord_*"            # glob on the recording's file name
    to = "anywayanyday-info-gate@yandex.ru"

    [[route]]                               # by the client recognized in the summary
    name = "Альянс"                         # label only
    to = "time001@aeroclub.team"            # a string or a list of addresses

    [[route.client]]                        # one client company of this route
    name = "Байер"                          # canonical name
    aliases = ["Bayer"]                     # other spellings for the «Компания:» line
    domains = ["bayer.ru", "bayer.com"]     # its e-mail domains (subdomains match)

A route may also list bare `domains` / `companies` not yet tied to a client:
they route mail but, having no company↔domain pair, are not shown to the LLM.
Clients with domains become the summary prompt's {KNOWN_EMAIL_DOMAINS} list
(«Байер, Bayer → bayer.ru, bayer.com»), so one table drives both.

A domain or company listed twice is rejected at startup, so a large table
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
class Client:
    name: str
    aliases: tuple[str, ...]
    domains: tuple[str, ...]


@dataclass(frozen=True)
class ClientRoute:
    to: tuple[str, ...]
    domains: tuple[str, ...]    # all of the route's domains (clients' + bare)
    companies: tuple[str, ...]  # all names/aliases, normalized (see normalize_company)
    clients: tuple[Client, ...] = ()
    name: str = ""              # the route's `name` (team label), "route #N" if absent


@dataclass(frozen=True)
class Routing:
    file_routes: tuple[tuple[str, tuple[str, ...]], ...] = ()
    client_routes: tuple[ClientRoute, ...] = ()
    default: tuple[str, ...] = ()
    file_route_names: tuple[tuple[str, str], ...] = ()  # (pattern, name) of [[file_route]]

    def known_domains(self) -> list[tuple[list[str], tuple[str, ...]]]:
        """(names, domains) of every client that has domains, for the summary prompt."""
        return [
            ([c.name, *c.aliases], c.domains)
            for route in self.client_routes for c in route.clients if c.domains
        ]


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

    file_routes, file_route_names = [], []
    for i, table in enumerate(data.get("file_route", []), 1):
        where = f"{source}: file_route #{i}"
        if set(table) - {"pattern", "to", "name"} or not str(table.get("pattern", "")).strip():
            raise RoutingError(f"{where} needs `pattern` and `to` (and an optional `name`)")
        pattern = table["pattern"].strip()
        file_routes.append((pattern, _addresses(table.get("to"), where)))
        file_route_names.append((pattern, str(table.get("name", "")).strip() or pattern))

    client_routes, seen_domains, seen_companies = [], {}, {}

    def take_domains(raws: list[str], where: str) -> list[str]:
        out = []
        for raw in raws:
            domain = raw.lower().lstrip("@")
            if not _DOMAIN_RE.match(domain):
                raise RoutingError(f"{source}: {where}: {raw!r} is not a domain")
            if domain in seen_domains:
                raise RoutingError(f"{source}: {where}: domain {domain!r} is already listed in {seen_domains[domain]}")
            seen_domains[domain] = where
            out.append(domain)
        return out

    def take_companies(raws: list[str], where: str) -> list[str]:
        out = []
        for raw in raws:
            key = normalize_company(raw)
            if not key:
                raise RoutingError(f"{source}: {where}: company {raw!r} is empty after normalization")
            if seen_companies.get(key) == where:
                continue  # another spelling of the same name in the same entry («Кока-Кола»/«Кока Кола»)
            if key in seen_companies:
                raise RoutingError(f"{source}: {where}: company {raw!r} is already listed in {seen_companies[key]}")
            seen_companies[key] = where
            out.append(key)
        return out

    for i, table in enumerate(data.get("route", []), 1):
        where = f"route #{i}"
        unknown = set(table) - {"to", "domains", "companies", "name", "client"}
        if unknown:
            raise RoutingError(f"{source}: {where}: unknown key(s) {', '.join(sorted(unknown))}")
        to = _addresses(table.get("to"), f"{source}: {where}")
        clients, domains, companies = [], [], []
        for j, client in enumerate(table.get("client", []), 1):
            cwhere = f"{where}, client #{j}"
            if not isinstance(client, dict):
                raise RoutingError(f"{source}: {cwhere} must be a table")
            unknown = set(client) - {"name", "aliases", "domains"}
            if unknown:
                raise RoutingError(f"{source}: {cwhere}: unknown key(s) {', '.join(sorted(unknown))}")
            name = str(client.get("name", "")).strip()
            if not name:
                raise RoutingError(f"{source}: {cwhere} needs a `name`")
            cwhere = f"{where}, client {name!r}"
            aliases = _strings(client, "aliases", f"{source}: {cwhere}")
            cdomains = take_domains(_strings(client, "domains", f"{source}: {cwhere}"), cwhere)
            companies += take_companies([name, *aliases], cwhere)
            domains += cdomains
            clients.append(Client(name, tuple(aliases), tuple(cdomains)))
        domains += take_domains(_strings(table, "domains", f"{source}: {where}"), where)
        companies += take_companies(_strings(table, "companies", f"{source}: {where}"), where)
        if not domains and not companies:
            raise RoutingError(f"{source}: {where} needs clients, `domains` and/or `companies`")
        name = str(table.get("name", "")).strip() or where
        client_routes.append(ClientRoute(to, tuple(domains), tuple(companies), tuple(clients), name))
    return Routing(tuple(file_routes), tuple(client_routes), default, tuple(file_route_names))


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


# Whisper sometimes writes a dictated address as one Latin word with the "@"
# spelled inside: mariasobachkax5.ru, annasobakabayer.com
_GLUED_EMAIL = re.compile(
    r"[a-z0-9._+-]+?sobac?h?ka[-_.]?([a-z0-9-]+(?:\.[a-z0-9-]+)+)", re.IGNORECASE
)


def client_email_domain(summary: str) -> str:
    """Domain of the client's address: the «Почта:» line, else the first address in the text."""
    line = _field(summary, "Почта")
    text = summary if line is None else line  # «Почта: не указано» → no address, don't look further
    match = _EMAIL.search(text) or _GLUED_EMAIL.search(text)
    return match.group(1).lower() if match else ""


def _route_for_domain(routing: Routing, domain: str) -> ClientRoute | None:
    """Most specific route whose domain is `domain` or its parent."""
    best, best_len = None, 0
    for route in routing.client_routes:
        for d in route.domains:
            if (domain == d or domain.endswith("." + d)) and len(d) > best_len:
                best, best_len = route, len(d)
    return best


def _route_in_transcript(routing: Routing, text: str) -> ClientRoute | None:
    """Route of an address spelled in `text` (a@x5.ru, annasobakabayer.com → bayer.com).

    A last resort for addresses the summary lost: a client's domain written out
    in the transcript is strong evidence even when the summary says «не указано».
    """
    for regex in (_EMAIL, _GLUED_EMAIL):
        for match in regex.finditer(text):
            route = _route_for_domain(routing, match.group(1).lower())
            if route is not None:
                return route
    return None


_NOT_GIVEN = normalize_company("не указано")


def client_company(summary: str) -> str:
    value = _field(summary, "Компания") or ""
    return "" if normalize_company(value) in ("", _NOT_GIVEN) else value


def match_client(
    routing: Routing, summary: str, transcript: str = ""
) -> tuple[ClientRoute | None, str]:
    """(route, "domain" | "company" | "transcript") for the client, (None, "") if none.

    Most specific domain match in the summary first, then a company-name match,
    then a client's address spelled out in the transcript.
    """
    if not routing.client_routes or not (summary or transcript):
        return None, ""
    domain = client_email_domain(summary)
    if domain:
        route = _route_for_domain(routing, domain)
        if route is not None:
            return route, "domain"
    company = normalize_company(client_company(summary))
    if company:
        # whole-word match; the longest alias wins («Дельта Лизинг» over «Дельта»)
        padded = f" {company} "
        best, best_len = None, 0
        for route in routing.client_routes:
            for c in route.companies:
                if f" {c} " in padded and len(c) > best_len:
                    best, best_len = route, len(c)
        if best is not None:
            return best, "company"
    route = _route_in_transcript(routing, transcript)
    if route is not None:
        return route, "transcript"
    return None, ""


def route_for_client(routing: Routing, summary: str, transcript: str = "") -> ClientRoute | None:
    return match_client(routing, summary, transcript)[0]
