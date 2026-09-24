"""The names places go by, and the codes they are stored under (task P2-23).

Reference data, not configuration: which country a name denotes is settled by
ISO 3166-1, not by an operator, so this lives in code beside the matcher rather
than in a seeded table that could drift from it. Changing anything here changes
:func:`meridian_core.places.basis_fingerprint`, so every source is re-examined
under the new vocabulary — the same rule `topiclabels.REFERENCE_TEXTS` follows.

**The code scheme.** A place is stored as a short uppercase code:

- a **country** is its ISO 3166-1 alpha-2 code — two letters. ``EU`` is the
  code ISO reserves for the European Union, and is used for it; a document
  about union-level regulation is about neither one member state nor none.
- a **city** is its UN/LOCODE, written without the space — five characters,
  the country's alpha-2 followed by three. The prefix *is* the country, so a
  city code always implies its country code and a consumer can roll a city up
  with ``code[:2]``. Length alone tells the two kinds apart, and neither can be
  confused with an ISO 3166-2 subdivision code, which carries a hyphen.

City-states (and territories ISO codes as countries) have no separate city
code: the country code already names the city.

**Names are matched case-sensitively, as proper nouns.** Lower-case forms are
overwhelmingly common nouns and adjectives; an all-caps form is matched too,
because headings and some PDFs set names in capitals.

**What is left out, and why.** A surface form that is routinely something other
than the place — a common first name or surname, a US state that is also a
country, a word that is also a bird — is listed in :data:`AMBIGUOUS` and never
matched. Precision is the point: a place tag that is wrong puts a document in
the wrong column of a comparison, while a missing tag leaves it out of one and
the pass's other signals may still supply it. Demonyms and adjectives are not
matched at all: most of them are also the name of a language, and a document
that quotes a foreign-language title is not about that country.

:data:`NOT_PLACES` are phrases that *contain* a place name and are not about
the place — a treaty named for the city it was signed in, a newspaper named
for its city. The matcher consumes them first, so the name inside is never
counted.
"""

from __future__ import annotations

#: ISO 3166-1 alpha-2 → the English names that denote it. First is the display
#: name. Every name here is matched unless it is also in :data:`AMBIGUOUS`.
COUNTRIES: dict[str, tuple[str, ...]] = {
    "AD": ("Andorra",),
    "AE": ("United Arab Emirates", "UAE", "U.A.E."),
    "AF": ("Afghanistan",),
    "AG": ("Antigua and Barbuda",),
    "AL": ("Albania",),
    "AM": ("Armenia",),
    "AO": ("Angola",),
    "AR": ("Argentina",),
    "AT": ("Austria",),
    "AU": ("Australia",),
    "AZ": ("Azerbaijan",),
    "BA": ("Bosnia and Herzegovina", "Bosnia"),
    "BB": ("Barbados",),
    "BD": ("Bangladesh",),
    "BE": ("Belgium",),
    "BF": ("Burkina Faso",),
    "BG": ("Bulgaria",),
    "BH": ("Bahrain",),
    "BI": ("Burundi",),
    "BJ": ("Benin",),
    "BN": ("Brunei", "Brunei Darussalam"),
    "BO": ("Bolivia",),
    "BR": ("Brazil",),
    "BS": ("Bahamas",),
    "BT": ("Bhutan",),
    "BW": ("Botswana",),
    "BY": ("Belarus",),
    "BZ": ("Belize",),
    "CA": ("Canada",),
    "CD": ("Democratic Republic of the Congo", "DR Congo"),
    "CF": ("Central African Republic",),
    "CG": ("Republic of the Congo",),
    "CH": ("Switzerland",),
    "CI": ("Côte d'Ivoire", "Ivory Coast"),
    "CL": ("Chile",),
    "CM": ("Cameroon",),
    "CN": ("China", "People's Republic of China", "Mainland China", "PRC"),
    "CO": ("Colombia",),
    "CR": ("Costa Rica",),
    "CU": ("Cuba",),
    "CV": ("Cabo Verde", "Cape Verde"),
    "CY": ("Cyprus",),
    "CZ": ("Czechia", "Czech Republic"),
    "DE": ("Germany",),
    "DJ": ("Djibouti",),
    "DK": ("Denmark",),
    "DM": ("Dominica",),
    "DO": ("Dominican Republic",),
    "DZ": ("Algeria",),
    "EC": ("Ecuador",),
    "EE": ("Estonia",),
    "EG": ("Egypt",),
    "ER": ("Eritrea",),
    "ES": ("Spain",),
    "ET": ("Ethiopia",),
    "EU": ("European Union",),
    "FI": ("Finland",),
    "FJ": ("Fiji",),
    "FM": ("Micronesia",),
    "FR": ("France",),
    "GA": ("Gabon",),
    "GB": (
        "United Kingdom",
        "UK",
        "U.K.",
        "Great Britain",
        "Britain",
        "England",
        "Scotland",
        "Wales",
        "Northern Ireland",
    ),
    "GD": ("Grenada",),
    "GE": ("Georgia",),
    "GH": ("Ghana",),
    "GL": ("Greenland",),
    "GM": ("Gambia", "The Gambia"),
    "GN": ("Guinea",),
    "GQ": ("Equatorial Guinea",),
    "GR": ("Greece",),
    "GT": ("Guatemala",),
    "GU": ("Guam",),
    "GW": ("Guinea-Bissau",),
    "GY": ("Guyana",),
    "HK": ("Hong Kong", "HKSAR"),
    "HN": ("Honduras",),
    "HR": ("Croatia",),
    "HT": ("Haiti",),
    "HU": ("Hungary",),
    "ID": ("Indonesia",),
    "IE": ("Ireland", "Republic of Ireland"),
    "IL": ("Israel",),
    "IN": ("India",),
    "IQ": ("Iraq",),
    "IR": ("Iran",),
    "IS": ("Iceland",),
    "IT": ("Italy",),
    "JM": ("Jamaica",),
    "JO": ("Jordan",),
    "JP": ("Japan",),
    "KE": ("Kenya",),
    "KG": ("Kyrgyzstan",),
    "KH": ("Cambodia",),
    "KI": ("Kiribati",),
    "KM": ("Comoros",),
    "KN": ("Saint Kitts and Nevis",),
    "KP": ("North Korea", "DPRK"),
    "KR": ("South Korea", "Republic of Korea", "Korea"),
    "KW": ("Kuwait",),
    "KZ": ("Kazakhstan",),
    "LA": ("Laos", "Lao PDR"),
    "LB": ("Lebanon",),
    "LC": ("Saint Lucia",),
    "LI": ("Liechtenstein",),
    "LK": ("Sri Lanka",),
    "LR": ("Liberia",),
    "LS": ("Lesotho",),
    "LT": ("Lithuania",),
    "LU": ("Luxembourg",),
    "LV": ("Latvia",),
    "LY": ("Libya",),
    "MA": ("Morocco",),
    "MC": ("Monaco",),
    "MD": ("Moldova",),
    "ME": ("Montenegro",),
    "MG": ("Madagascar",),
    "MH": ("Marshall Islands",),
    "MK": ("North Macedonia",),
    "ML": ("Mali",),
    "MM": ("Myanmar", "Burma"),
    "MN": ("Mongolia",),
    "MO": ("Macau", "Macao"),
    "MR": ("Mauritania",),
    "MT": ("Malta",),
    "MU": ("Mauritius",),
    "MV": ("Maldives",),
    "MW": ("Malawi",),
    "MX": ("Mexico",),
    "MY": ("Malaysia",),
    "MZ": ("Mozambique",),
    "NA": ("Namibia",),
    "NE": ("Niger",),
    "NG": ("Nigeria",),
    "NI": ("Nicaragua",),
    "NL": ("Netherlands", "The Netherlands", "Holland"),
    "NO": ("Norway",),
    "NP": ("Nepal",),
    "NR": ("Nauru",),
    "NZ": ("New Zealand", "Aotearoa"),
    "OM": ("Oman",),
    "PA": ("Panama",),
    "PE": ("Peru",),
    "PG": ("Papua New Guinea",),
    "PH": ("Philippines",),
    "PK": ("Pakistan",),
    "PL": ("Poland",),
    "PR": ("Puerto Rico",),
    "PS": ("Palestine",),
    "PT": ("Portugal",),
    "PW": ("Palau",),
    "PY": ("Paraguay",),
    "QA": ("Qatar",),
    "RO": ("Romania",),
    "RS": ("Serbia",),
    "RU": ("Russia", "Russian Federation"),
    "RW": ("Rwanda",),
    "SA": ("Saudi Arabia",),
    "SB": ("Solomon Islands",),
    "SC": ("Seychelles",),
    "SD": ("Sudan",),
    "SE": ("Sweden",),
    "SG": ("Singapore",),
    "SI": ("Slovenia",),
    "SK": ("Slovakia",),
    "SL": ("Sierra Leone",),
    "SM": ("San Marino",),
    "SN": ("Senegal",),
    "SO": ("Somalia",),
    "SR": ("Suriname",),
    "SS": ("South Sudan",),
    "SV": ("El Salvador",),
    "SY": ("Syria",),
    "SZ": ("Eswatini", "Swaziland"),
    "TD": ("Chad",),
    "TG": ("Togo",),
    "TH": ("Thailand",),
    "TJ": ("Tajikistan",),
    "TL": ("Timor-Leste", "East Timor"),
    "TM": ("Turkmenistan",),
    "TN": ("Tunisia",),
    "TO": ("Tonga",),
    "TR": ("Türkiye", "Turkey"),
    "TT": ("Trinidad and Tobago",),
    "TV": ("Tuvalu",),
    "TW": ("Taiwan",),
    "TZ": ("Tanzania",),
    "UA": ("Ukraine",),
    "UG": ("Uganda",),
    "US": (
        "United States",
        "United States of America",
        "U.S.",
        "U.S.A.",
        "USA",
        "America",
    ),
    "UY": ("Uruguay",),
    "UZ": ("Uzbekistan",),
    "VA": ("Vatican City", "Holy See"),
    "VC": ("Saint Vincent and the Grenadines",),
    "VE": ("Venezuela",),
    "VN": ("Vietnam", "Viet Nam"),
    "VU": ("Vanuatu",),
    "WS": ("Samoa",),
    "YE": ("Yemen",),
    "ZA": ("South Africa",),
    "ZM": ("Zambia",),
    "ZW": ("Zimbabwe",),
}

#: UN/LOCODE (no space) → the names that denote the city. The first two letters
#: are the country. Large metropolitan areas, not a hand-picked list: a city
#: missing here is still tagged with its country when the country is named,
#: and the list grows by adding a line, which re-examines the corpus.
CITIES: dict[str, tuple[str, ...]] = {
    # East Asia
    "JPTYO": ("Tokyo",),
    "JPOSA": ("Osaka",),
    "JPYOK": ("Yokohama",),
    "JPNGO": ("Nagoya",),
    "JPFUK": ("Fukuoka",),
    "JPSPK": ("Sapporo",),
    "JPUKY": ("Kyoto",),
    "KRSEL": ("Seoul",),
    "KRPUS": ("Busan",),
    "KRINC": ("Incheon",),
    "CNBJS": ("Beijing",),
    "CNSHA": ("Shanghai",),
    "CNSZX": ("Shenzhen",),
    "CNCAN": ("Guangzhou",),
    "CNCTU": ("Chengdu",),
    "CNWUH": ("Wuhan",),
    "CNHGH": ("Hangzhou",),
    "CNCKG": ("Chongqing",),
    "CNTSN": ("Tianjin",),
    "TWTPE": ("Taipei",),
    # South-east and south Asia
    "THBKK": ("Bangkok",),
    "MYKUL": ("Kuala Lumpur",),
    "IDJKT": ("Jakarta",),
    "IDSUB": ("Surabaya",),
    "PHMNL": ("Manila", "Metro Manila"),
    "VNSGN": ("Ho Chi Minh City", "Saigon"),
    "VNHAN": ("Hanoi",),
    "INDEL": ("Delhi", "New Delhi"),
    "INBOM": ("Mumbai",),
    "INBLR": ("Bengaluru", "Bangalore"),
    "INMAA": ("Chennai",),
    "INCCU": ("Kolkata",),
    "INHYD": ("Hyderabad",),
    # Middle East and Africa
    "AEDXB": ("Dubai",),
    "AEAUH": ("Abu Dhabi",),
    "QADOH": ("Doha",),
    "SARUH": ("Riyadh",),
    "TRIST": ("Istanbul",),
    "EGCAI": ("Cairo",),
    "NGLOS": ("Lagos",),
    "KENBO": ("Nairobi",),
    "ZAJNB": ("Johannesburg",),
    "ZACPT": ("Cape Town",),
    # Oceania
    "AUSYD": ("Sydney",),
    "AUMEL": ("Melbourne",),
    "AUBNE": ("Brisbane",),
    "AUPER": ("Perth",),
    "AUADL": ("Adelaide",),
    "AUDRW": ("Darwin", "City of Darwin"),
    "NZAKL": ("Auckland",),
    "NZWLG": ("Wellington",),
    # Europe
    "GBLON": ("London", "Greater London"),
    "GBMNC": ("Manchester", "Greater Manchester"),
    "GBBHM": ("Birmingham",),
    "GBEDI": ("Edinburgh",),
    "GBGLW": ("Glasgow",),
    "IEDUB": ("Dublin",),
    "FRPAR": ("Paris", "Île-de-France"),
    "FRLYS": ("Lyon",),
    "FRMRS": ("Marseille",),
    "DEBER": ("Berlin",),
    "DEHAM": ("Hamburg",),
    "DEMUC": ("Munich", "München"),
    "DEFRA": ("Frankfurt",),
    "DECGN": ("Cologne", "Köln"),
    "NLAMS": ("Amsterdam",),
    "NLRTM": ("Rotterdam",),
    "NLHAG": ("The Hague",),
    "NLUTC": ("Utrecht",),
    "BEBRU": ("Brussels",),
    "BEANR": ("Antwerp",),
    "DKCPH": ("Copenhagen",),
    "SESTO": ("Stockholm",),
    "SEGOT": ("Gothenburg",),
    "NOOSL": ("Oslo",),
    "FIHEL": ("Helsinki",),
    "ATVIE": ("Vienna",),
    "CHZRH": ("Zurich", "Zürich"),
    "CHGVA": ("Geneva",),
    "ESMAD": ("Madrid",),
    "ESBCN": ("Barcelona",),
    "PTLIS": ("Lisbon",),
    "ITROM": ("Rome",),
    "ITMIL": ("Milan",),
    "PLWAW": ("Warsaw",),
    "CZPRG": ("Prague",),
    "GRATH": ("Athens",),
    # The Americas
    "USNYC": ("New York City", "New York", "NYC"),
    "USLAX": ("Los Angeles",),
    "USCHI": ("Chicago",),
    "USSFO": ("San Francisco", "San Francisco Bay Area"),
    "USWAS": ("Washington, D.C.", "Washington DC", "Washington, DC"),
    "USBOS": ("Boston",),
    "USSEA": ("Seattle",),
    "USMIA": ("Miami",),
    "USHOU": ("Houston",),
    "USATL": ("Atlanta",),
    "USDAL": ("Dallas",),
    "USDEN": ("Denver",),
    "USPIT": ("Pittsburgh",),
    "USPHL": ("Philadelphia",),
    "USSAN": ("San Diego",),
    "USDET": ("Detroit",),
    "CATOR": ("Toronto",),
    "CAVAN": ("Vancouver",),
    "CAMTR": ("Montreal", "Montréal"),
    "MXMEX": ("Mexico City",),
    "BRSAO": ("São Paulo", "Sao Paulo"),
    "BRRIO": ("Rio de Janeiro",),
    "ARBUE": ("Buenos Aires",),
    "CLSCL": ("Santiago de Chile",),
    "COBOG": ("Bogotá", "Bogota"),
    "PELIM": ("Lima",),
}

#: Country → the names of its states, provinces and regions. Not stored as
#: codes of their own — ISO 3166-2 would give them one, and nothing here asks
#: questions at that level yet — but a document about a state's regulation is
#: about the country, and without these it names the country too rarely to
#: tag. Several contain another place's name, which is why they must be
#: matched whole.
SUBDIVISIONS: dict[str, tuple[str, ...]] = {
    "US": (
        "Alabama",
        "Alaska",
        "Arizona",
        "Arkansas",
        "California",
        "Colorado",
        "Connecticut",
        "Delaware",
        "Florida",
        "Hawaii",
        "Idaho",
        "Illinois",
        "Indiana",
        "Iowa",
        "Kansas",
        "Kentucky",
        "Louisiana",
        "Maine",
        "Maryland",
        "Massachusetts",
        "Michigan",
        "Minnesota",
        "Mississippi",
        "Missouri",
        "Montana",
        "Nebraska",
        "Nevada",
        "New Hampshire",
        "New Jersey",
        "New Mexico",
        "North Carolina",
        "North Dakota",
        "Ohio",
        "Oklahoma",
        "Oregon",
        "Pennsylvania",
        "Rhode Island",
        "South Carolina",
        "South Dakota",
        "Tennessee",
        "Texas",
        "Utah",
        "Vermont",
        "Virginia",
        "West Virginia",
        "Wisconsin",
        "Wyoming",
        "New England",
    ),
    "AU": (
        "New South Wales",
        "Queensland",
        "Tasmania",
        "Western Australia",
        "South Australia",
        "Northern Territory",
        "Australian Capital Territory",
    ),
    "CA": (
        "Ontario",
        "Quebec",
        "Québec",
        "British Columbia",
        "Alberta",
        "Manitoba",
        "Saskatchewan",
        "Nova Scotia",
        "New Brunswick",
        "Newfoundland",
    ),
    "DE": ("Bavaria", "Baden-Württemberg", "North Rhine-Westphalia", "Lower Saxony"),
    "CN": ("Guangdong", "Zhejiang", "Jiangsu", "Sichuan", "Hubei", "Shandong"),
}

#: Surface forms never matched: each is routinely something other than the
#: place. Their countries and cities are still reachable through their other
#: names, through the publisher's domain, and through cited entities.
AMBIGUOUS: frozenset[str] = frozenset(
    {
        "America",  # the continent, and the Americas
        "Georgia",  # a US state as often as a country
        "Jordan",  # a personal name
        "Chad",  # a personal name
        "Guinea",  # inside other countries' names and a common noun
        "Korea",  # both Koreas; the specific names are matched
        "Darwin",  # a surname, far more often than the city in research text
        "Perth",  # two countries' cities
        "Birmingham",  # two countries' cities
        "Lima",  # also a common word in other languages
        "Wellington",  # a surname and many smaller places
        "Holland",  # also a surname
        "Mali",
        "Niger",
        "Turkey",
        "Palestine",  # also several smaller places
        "PRC",  # also an unrelated acronym in several fields
        "DPRK",
    }
)

#: Phrases that contain a place name and are not about the place. Consumed by
#: the matcher so the name inside them is not counted.
NOT_PLACES: tuple[str, ...] = (
    "Paris Agreement",
    "Paris Climate Agreement",
    "Kyoto Protocol",
    "Vienna Convention",
    "Geneva Convention",
    "Geneva Conventions",
    "Washington Consensus",
    "New York Times",
    "Washington Post",
    "Los Angeles Times",
    "Chicago Tribune",
    "Boston Globe",
    "Financial Times",
    "Chicago style",
    "Chicago Manual of Style",
    # Publishers named for the city they print in, as reference lists and
    # imprint lines write them.
    "Springer Berlin Heidelberg",
    "Berlin Heidelberg",
    "Berlin, Heidelberg",
    "Springer Nature Singapore",
    "Springer Singapore",
    "World Scientific, Singapore",
)

#: Country-code top-level domains that are sold as generic names and say
#: nothing about where a publisher is.
GENERIC_CCTLDS: frozenset[str] = frozenset(
    {"io", "co", "tv", "me", "ai", "ly", "fm", "am", "to", "cc", "ws", "gg", "la", "nu", "tk"}
    | {"ag", "sh", "vc", "so", "gl", "is", "im", "app", "dev"}
)

#: Top-level domains that are not two letters but do name a country.
TLD_COUNTRY: dict[str, str] = {
    "uk": "GB",
    "gov": "US",
    "mil": "US",
    "edu": "US",
}

#: Languages that point at a small set of countries — weak evidence only, and
#: never a tag on its own. English is not here: it is written everywhere.
LANGUAGE_COUNTRIES: dict[str, frozenset[str]] = {
    "ja": frozenset({"JP"}),
    "ko": frozenset({"KR"}),
    "th": frozenset({"TH"}),
    "id": frozenset({"ID"}),
    "ms": frozenset({"MY", "BN", "SG"}),
    "zh": frozenset({"CN", "TW", "HK", "SG", "MO"}),
    "vi": frozenset({"VN"}),
    "de": frozenset({"DE", "AT", "CH", "LI", "LU"}),
    "fr": frozenset({"FR", "BE", "CH", "LU", "MC", "CA"}),
    "nl": frozenset({"NL", "BE"}),
    "it": frozenset({"IT", "CH", "SM", "VA"}),
    "es": frozenset({"ES", "MX", "AR", "CO", "CL", "PE", "VE", "EC", "UY", "PY", "BO"}),
    "pt": frozenset({"PT", "BR"}),
    "sv": frozenset({"SE", "FI"}),
    "da": frozenset({"DK"}),
    "nb": frozenset({"NO"}),
    "no": frozenset({"NO"}),
    "fi": frozenset({"FI"}),
    "pl": frozenset({"PL"}),
    "cs": frozenset({"CZ"}),
    "el": frozenset({"GR", "CY"}),
    "tr": frozenset({"TR"}),
    "he": frozenset({"IL"}),
    "ko-kr": frozenset({"KR"}),
}


def country_of(code: str) -> str:
    """The country a code belongs to: itself for a country, its prefix for a city."""
    return code[:2]


def is_city(code: str) -> bool:
    return len(code) == 5


def display_name(code: str) -> str:
    """The name a code is shown as. An unknown code is shown as itself."""
    names = CITIES.get(code) or COUNTRIES.get(code)
    return names[0] if names else code
