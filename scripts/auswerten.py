#!/usr/bin/env python3
# Klein_Method Schwarmwissen - Auswertung der Laufberichte
#
# Liest alle Issues des Repositorys, erkennt Laufberichte an der Kennung
# "KLEIN_METHOD_LAUFBERICHT format=1", prueft das Format und schreibt
# erfahrungswerte.txt: je Typ|Klasse|Komponente|Projekt der Wert des letzten
# Laufs mit DONE (frei/fix: Geraeteklasse; dynamisch: Festpunktart, Punktwerte). Die 10-%-Regel wendet
# Klein_Method selbst an. Gueltige Berichte bekommen das Label "laufbericht",
# ungueltige "ungueltig". Issues mit Label "ungueltig" werden nie gezaehlt
# (Moderation von Hand). Nur Python-Standardbibliothek.
import json
import os
import re
import sys
import urllib.request

MARKER = "KLEIN_METHOD_LAUFBERICHT format=1"
TYPES = {
    "HORIZONTAL_DIRECTION_LOG_POST", "HORIZONTAL_DISTANCE_LOG_POST", "VERTICAL_DIRECTION_LOG_POST",
    "SLOPE_DISTANCE_LOG_POST", "NIVELLEMENT_LOG_POST",
    "GNSS_BASELINE_1D_LOG_POST", "GNSS_BASELINE_2D_LOG_POST", "GNSS_BASELINE_3D_LOG_POST",
}
# V0.396: dynamische AGL - Punktwerte je Dimension, Komponente "s", Klasse = Festpunktart
POINT_TYPES = {f"PUNKT{kind}_{d}D" for kind in ("GRUPPE", "MEDIAN") for d in (1, 2, 3)}
DEVICE_CLASSES = {"ELEKTRONISCH", "HISTORISCH", "KOMBINIERT"}
FIXED_POINT_CLASSES = {"GNSS_REFERENZ", "TP_AP", "HOEHENFESTPUNKT", "SONSTIGE"}
SIGMA_KEY = re.compile(r"^sigma\.([A-Z0-9_]+)\.([abcs])$")
PROJECT = re.compile(r"^[0-9a-f]{12}$")
OUTPUT = "erfahrungswerte.txt"
# Plausibilitaetsgrenze je Genauigkeit (mgon, mm, ppm) - offenes Repository: unsinnige Werte nicht zaehlen
MAX_VALUE = 1000.0

TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPOSITORY = os.environ.get("REPOSITORY", "")
API = "https://api.github.com"


def request(method, url, data=None):
    body = None if data is None else json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if TOKEN:
        req.add_header("Authorization", "Bearer " + TOKEN)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as response:
        text = response.read().decode("utf-8")
        return json.loads(text) if text else None


def all_issues():
    # Test ohne GitHub: LOCAL_ISSUES_JSON=datei.json
    local = os.environ.get("LOCAL_ISSUES_JSON")
    if local:
        with open(local, encoding="utf-8") as f:
            return json.load(f)
    issues, page = [], 1
    while True:
        batch = request("GET", f"{API}/repos/{REPOSITORY}/issues?state=all&per_page=100&page={page}")
        if not batch:
            break
        issues.extend(i for i in batch if "pull_request" not in i)
        page += 1
    return issues


def parse_report(body):
    if not body or MARKER not in body:
        return None
    match = re.search(r"```[a-z]*\n(.*?)```", body, re.S)
    if not match:
        return {}
    report = {}
    for line in match.group(1).splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            report[key.strip()] = value.strip()
    return report


def validate(report):
    """Liste der gueltigen (Typ, Komponente, Wert) oder None bei ungueltigem Bericht."""
    if report.get("format") != "1" or not PROJECT.match(report.get("projekt", "")):
        return None
    if report.get("geraeteklasse") not in DEVICE_CLASSES:
        return None
    dynamic = report.get("stufe") == "DYNAMIC"
    if dynamic and report.get("festpunktart") not in FIXED_POINT_CLASSES:
        return None
    if not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$", report.get("zeit", "")):
        return None
    values = []
    for key, value in report.items():
        m = SIGMA_KEY.match(key)
        if not m:
            continue
        allowed = POINT_TYPES if dynamic else TYPES
        if m.group(1) not in allowed or (m.group(2) == "s") != dynamic:
            return None
        try:
            number = float(value)
        except ValueError:
            return None
        if not (0.0 <= number <= MAX_VALUE):
            return None
        if number > 0.0:
            values.append((m.group(1), m.group(2), number))
    return values


def add_label(issue, label):
    names = {l["name"] for l in issue.get("labels", [])}
    if label in names or os.environ.get("LOCAL_ISSUES_JSON"):
        return
    try:
        request("POST", f"{API}/repos/{REPOSITORY}/issues/{issue['number']}/labels", {"labels": [label]})
    except Exception as error:  # Label ist nur Komfort
        print(f"Label {label} fuer #{issue['number']} nicht gesetzt: {error}")


def main():
    latest = {}   # (typ, klasse, komponente, projekt) -> (zeit, wert)
    valid = invalid = counted = 0
    for issue in all_issues():
        report = parse_report(issue.get("body") or "")
        if report is None:
            continue
        labels = {l["name"] for l in issue.get("labels", [])}
        values = validate(report) if report else None
        if values is None:
            invalid += 1
            add_label(issue, "ungueltig")
            continue
        valid += 1
        add_label(issue, "laufbericht")
        if "ungueltig" in labels:
            continue
        if report.get("done") != "true" or report.get("verworfen") == "true":
            continue
        # ab V0.432: Nachlauf ohne Grenzwert ("bester Stand") zaehlt nicht; aeltere Berichte ohne den Schluessel zaehlen
        if report.get("grenzwert_erreicht") == "false":
            continue
        if report.get("stufe") not in ("FREE", "FIXED", "DYNAMIC"):
            continue
        counted += 1
        klasse = report["festpunktart"] if report.get("stufe") == "DYNAMIC" else report["geraeteklasse"]
        for type_name, component, number in values:
            key = (type_name, klasse, component, report["projekt"])
            if key not in latest or latest[key][0] <= report["zeit"]:
                latest[key] = (report["zeit"], number)
    lines = [
        "# Klein_Method Schwarmwissen - automatisch erzeugt, nicht von Hand bearbeiten",
        f"# Berichte gueltig {valid}, ungueltig {invalid}, gezaehlt (DONE, nicht verworfen, Grenzwert erreicht) {counted}",
        "# Typ|Klasse|Komponente|Projekt=Wert (Einheit wie in Klein_Method: mgon/mm/ppm; Punktwerte mm)",
    ]
    for key in sorted(latest):
        lines.append("|".join(key) + "=" + repr(latest[key][1]))
    with open(OUTPUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print(f"{OUTPUT}: {len(latest)} Werte aus {counted} Berichten (gueltig {valid}, ungueltig {invalid}).")


if __name__ == "__main__":
    if not REPOSITORY and not os.environ.get("LOCAL_ISSUES_JSON"):
        sys.exit("REPOSITORY fehlt")
    main()
