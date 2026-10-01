from pathlib import Path

import pytest

from ai_service import mailer
from ai_service.config import ConfigError, load_config
from ai_service.routing import (
    RoutingError, client_company, client_email_domain, load_routing_file, normalize_company,
    parse_routing, route_for_client,
)

REPO_FILE = Path(__file__).resolve().parents[2] / "config" / "email-routing.toml"


def summary(company="не указано", email="не указано"):
    return (f"Имя: Иван Петров\nКомпания: {company}\nПочта: {email}\n"
            "Суть обращения: Нужен билет.\nДетали: нет\nСрочность: не указана")


@pytest.mark.parametrize("raw,key", [
    ("«Х5 Group»", "x5 group"), ("X5 Group", "x5 group"),
    ("ООО \"Байер\"", normalize_company("байер")), ("Кока-Кола", normalize_company("кока кола")),
    ("Эн+", normalize_company("эн+")), ("Ёлка", normalize_company("елка")),
])
def test_normalize_company(raw, key):
    assert normalize_company(raw) == key


def test_summary_fields():
    s = summary("Байер", "ivan@bayer.ru (продиктовано: «…»)")
    assert (client_company(s), client_email_domain(s)) == ("Байер", "bayer.ru")
    assert (client_company(summary()), client_email_domain(summary())) == ("", "")
    # no «Почта:» line (e.g. the call profile): first address anywhere
    assert client_email_domain("Клиент из X5 просит счёт на a@x5.ru") == "x5.ru"
    # «Почта: не указано» → don't pick a colleague's address from the text
    assert client_email_domain("Почта: не указано\nСуть: переслать boss@sibur.ru") == ""


ROUTING = parse_routing({
    "default": "time017@aeroclub.team",
    "route": [
        {"to": "time006@aeroclub.team", "companies": ["Дельта Лизинг"], "domains": ["deltaleasing.ru"]},
        {"to": "time007@aeroclub.team", "companies": ["Дельта", "Лента"],
         "domains": ["lenta.com", "mvideo.ru"]},
        {"to": "time003@aeroclub.team", "domains": ["irkutskenergo.ru"]},
        {"to": "time009@aeroclub.team", "domains": ["es.irkutskenergo.ru"]},
    ],
})


@pytest.mark.parametrize("company,email,to", [
    ("не указано", "ivan@lenta.com", "time007@aeroclub.team"),
    ("не указано", "ivan@shop.mvideo.ru (проверить)", "time007@aeroclub.team"),  # subdomain
    ("не указано", "ivan@es.irkutskenergo.ru", "time009@aeroclub.team"),        # most specific
    ("не указано", "ivan@irkutskenergo.ru", "time003@aeroclub.team"),
    ("не указано", "ivan@notlenta.com", None),
    ("Лента", "не указано", "time007@aeroclub.team"),                           # company fallback
    ("ООО «Дельта Лизинг»", "не указано", "time006@aeroclub.team"),            # longest name wins
    ("Дельта", "не указано", "time007@aeroclub.team"),
    ("Дельтализинг", "не указано", None),                                       # whole words only
    ("Лента", "ivan@deltaleasing.ru", "time006@aeroclub.team"),                 # domain beats company
    ("Аэроклуб", "kristina@aeroclub.ru", None),
])
def test_route_for_client(company, email, to):
    route = route_for_client(ROUTING, summary(company, email))
    assert (route.to[0] if route else None) == to


@pytest.mark.parametrize("data,match", [
    ({"routes": []}, "unknown key"),
    ({"route": [{"to": "a@x.ru"}]}, "needs `domains` and/or `companies`"),
    ({"route": [{"to": "nobody", "domains": ["x.ru"]}]}, "invalid address"),
    ({"route": [{"to": [], "domains": ["x.ru"]}]}, "`to` must be"),
    ({"route": [{"to": "a@x.ru", "domains": ["x"]}]}, "is not a domain"),
    ({"route": [{"to": "a@x.ru", "domain": ["x.ru"]}]}, "unknown key"),
    ({"route": [{"to": "a@x.ru", "domains": ["x.ru"]}, {"to": "b@x.ru", "domains": ["@X.ru"]}]},
     "already in route #1"),
    ({"route": [{"to": "a@x.ru", "companies": ["Х5"]}, {"to": "b@x.ru", "companies": ["X5"]}]},
     "already in route #1"),
    ({"file_route": [{"pattern": "A_*"}]}, "`to` must be"),
])
def test_invalid_routing_rejected(data, match):
    with pytest.raises(RoutingError, match=match):
        parse_routing(data)


def test_load_config_reads_the_file_and_merges_file_routes(tmp_path):
    from tests.ai_service.test_config import EMAIL, REQUIRED

    path = tmp_path / "routing.toml"
    path.write_text('default = "d@x.ru"\n[[file_route]]\npattern = "GATE_*"\nto = "g@x.ru"\n'
                    '[[route]]\nto = "s@x.ru"\ndomains = ["sibur.ru"]\n', encoding="utf-8")
    cfg = load_config({**REQUIRED, **EMAIL, "EMAIL_ROUTES": "AWAD_*=a@x.ru",
                       "EMAIL_ROUTING_FILE": str(path)})
    assert cfg.email_routes == (("AWAD_*", ("a@x.ru",)), ("GATE_*", ("g@x.ru",)))
    assert mailer.recipients_for(cfg, "s3://c/GATE_1.wav", summary(email="i@sibur.ru")) == ("g@x.ru",)
    assert mailer.recipients_for(cfg, "s3://c/IVR_1.wav", summary(email="i@sibur.ru")) == ("s@x.ru",)
    assert mailer.recipients_for(cfg, "s3://c/IVR_1.wav", summary()) == ("d@x.ru",)

    path.write_text("[[route]\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config({**REQUIRED, **EMAIL, "EMAIL_ROUTING_FILE": str(path)})
    with pytest.raises(ConfigError, match="cannot be read"):
        load_config({**REQUIRED, **EMAIL, "EMAIL_ROUTING_FILE": str(tmp_path / "missing.toml")})


# --- the committed routing table --------------------------------------------------------

@pytest.fixture(scope="module")
def repo_routing():
    return load_routing_file(str(REPO_FILE))  # also proves the file is valid


@pytest.mark.parametrize("company,email,to", [
    ("Байер", "не указано", "time001@aeroclub.team"),
    ("не указано", "anna@bayer.com", "time001@aeroclub.team"),
    ("Билайн", "не указано", "time002@aeroclub.team"),
    ("не указано", "petr@es.irkutskenergo.ru", "time003@aeroclub.team"),
    ("Эн+", "не указано", "time003@aeroclub.team"),
    ("Х5", "не указано", "time005@aeroclub.team"),
    ("не указано", "a.ivanova@x5.ru (проверить)", "time005@aeroclub.team"),
    ("Кока-кола", "не указано", "time006@aeroclub.team"),
    ("Дельта Лизинг", "не указано", "time006@aeroclub.team"),
    ("Дельта", "не указано", "time007@aeroclub.team"),
    ("не указано", "ivan@mvideo.ru", "time007@aeroclub.team"),
    ("Аэроклуб ИТ", "kristina.lukyanova@aeroclub.ru", "time017@aeroclub.team"),
    ("не указано", "kitsutsushir@gmail.com (проверить)", "time017@aeroclub.team"),
    ("не указано", "не указано", "time017@aeroclub.team"),
])
def test_committed_table(repo_routing, company, email, to):
    route = route_for_client(repo_routing, summary(company, email))
    assert (route.to[0] if route else repo_routing.default[0]) == to


def test_committed_file_routes(repo_routing):
    assert dict(repo_routing.file_routes) == {
        "AWAD_IVRrecord_*": ("anywayanyday-info-gate@yandex.ru",),
        "GATE_IVRrecord_*": ("info@go.gate.ru",),
    }
