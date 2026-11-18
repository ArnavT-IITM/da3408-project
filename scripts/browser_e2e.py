"""Real browser check of the frontend on https://localhost:8443.

Drives headless Chrome over the DevTools protocol with nothing mocked. Note that it presses
Start over, which clears replay scores (forecasts are kept). Writes evidence/ui/.
"""

import base64
import json
import ssl
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket

BASE = "https://localhost:8443"
CTX = ssl._create_unverified_context()
OUTPUT = Path(__file__).resolve().parents[1] / "evidence" / "ui"
OUTPUT.mkdir(parents=True, exist_ok=True)
checks, errors = [], []
state = {"seq": 0, "ws": None}


def api(path):
    with urllib.request.urlopen(BASE + "/api" + path, timeout=10, context=CTX) as response:
        return json.load(response)


def command(method, params=None):
    state["seq"] += 1
    ident = state["seq"]
    state["ws"].send(json.dumps({"id": ident, "method": method, "params": params or {}}))
    while True:
        message = json.loads(state["ws"].recv())
        if message.get("method") == "Runtime.exceptionThrown":
            errors.append(message["params"])
        if message.get("id") == ident:
            if "error" in message:
                raise RuntimeError(message["error"])
            return message.get("result", {})


def evaluate(expression):
    result = command(
        "Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True}
    )
    if "exceptionDetails" in result:
        raise RuntimeError(result["exceptionDetails"])
    return result["result"].get("value")


def check(name, expression):
    passed = bool(evaluate(expression))
    checks.append({"name": name, "passed": passed})
    if not passed:
        raise AssertionError(name)


def wait(expression, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if evaluate(expression):
            return
        time.sleep(0.2)
    raise AssertionError("Timed out waiting for: " + expression)


def screenshot(name):
    time.sleep(0.4)
    size = command("Page.getLayoutMetrics")["cssContentSize"]
    clip = {"x": 0, "y": 0, "width": size["width"], "height": size["height"], "scale": 1}
    data = command("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True, "clip": clip})
    (OUTPUT / name).write_bytes(base64.b64decode(data["data"]))


def viewport(width, mobile=False):
    command(
        "Emulation.setDeviceMetricsOverride",
        {"width": width, "height": 900, "deviceScaleFactor": 1, "mobile": mobile},
    )


report = {"frontend": BASE, "started_at_unix": time.time()}
chrome = None
try:
    if api("/status")["model"]["status"] != "ready":
        raise RuntimeError("The model is not ready.")
    chrome = subprocess.Popen(
        [
            "/usr/bin/google-chrome",
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--ignore-certificate-errors",
            "--remote-debugging-port=9828",
            "--remote-allow-origins=*",
            "--user-data-dir=" + tempfile.mkdtemp(),
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(50):
        try:
            targets = json.load(urllib.request.urlopen("http://127.0.0.1:9828/json"))
            target = next(item for item in targets if item["type"] == "page")
            break
        except Exception:
            time.sleep(0.2)
    state["ws"] = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=20)
    for domain in ["Page", "Runtime"]:
        command(f"{domain}.enable")
    viewport(1280)
    command("Page.navigate", {"url": BASE + "/#forecast"})

    # Forecast: one card per city for tomorrow, from the shared simulated date.
    evaluate("window.confirm = () => true")
    wait("document.querySelector('#sim-date').textContent !== '—'")
    evaluate("document.querySelector('#reset').click()")
    wait("document.querySelector('#sim-status').textContent.startsWith('Back at the start')")
    wait(
        "document.querySelectorAll('.fc .fc-aqi').length===3 && /Jan 1, 2020|1 Jan 2020/.test(document.querySelector('#fc-date').textContent)"
    )
    check(
        "Start over returns to the first simulated date",
        "document.querySelector('#sim-date').textContent.includes('2019')",
    )
    check(
        "Every city has a forecast for tomorrow",
        "[...document.querySelectorAll('.fc-aqi')].every(n => /^\\d/.test(n.textContent))",
    )
    check(
        "Each forecast is compared with today",
        "[...document.querySelectorAll('.fc-change')].every(n => /today \\(\\d/.test(n.textContent))",
    )
    check(
        "Each forecast shows its category and advice",
        "[...document.querySelectorAll('.fc')].every(c => c.querySelector('.fc-cat').textContent.length>2 && c.querySelector('.fc-guide').textContent.length>10)",
    )
    check("The AQI colour key is shown", "document.querySelectorAll('.aqi-key li').length===6")
    evaluate("document.querySelector('[data-advance=\"1\"]').click()")
    wait("document.querySelector('#sim-status').textContent.startsWith('Moved forward 1 day')", timeout=60)
    wait(
        "/Jan 2, 2020|2 Jan 2020/.test(document.querySelector('#fc-date').textContent) && document.querySelector('#fc-grid').getAttribute('aria-busy')==='false'"
    )
    check(
        "Next day moves the forecast to the following day",
        "/Jan 1, 2020|1 Jan 2020/.test(document.querySelector('#sim-date').textContent) && document.querySelectorAll('.fc .fc-aqi').length===3",
    )
    screenshot("forecast-desktop.png")

    # Accuracy: same clock, advance, jump forward and back.
    evaluate("document.querySelector('#tab-accuracy').click()")
    check("The clock is shared with the Accuracy tab", "!document.querySelector('#clockbar').hidden")
    report["after_reset"] = api("/accuracy?city=Delhi")["by_city"]
    evaluate("document.querySelector('[data-advance=\"7\"]').click()")
    wait("document.querySelector('#sim-status').textContent.startsWith('Moved forward 7 days')", timeout=60)
    wait("document.querySelector('#chart svg') !== null")
    check(
        "Advancing moves the simulated date",
        "document.querySelector('#sim-date').textContent.includes('2020')",
    )
    check(
        "Per-city scores appear",
        "[...document.querySelectorAll('.city-score .big')].every(n => n.textContent.startsWith('±'))",
    )
    check(
        "Forecast and actual lines are drawn",
        "document.querySelectorAll('#chart path.forecast, #chart path.actual').length===2",
    )
    check("Scored days are listed", "document.querySelectorAll('#scored tr').length>=3")
    wait("!document.querySelector('#goto').disabled")
    evaluate(
        "document.querySelector('#goto-date').value='2020-01-20'; document.querySelector('#goto').click()"
    )
    wait("/Jan 20|20 Jan/.test(document.querySelector('#sim-date').textContent)", timeout=60)
    check(
        "Go to a later date plays forward to it",
        "/Jan 20|20 Jan/.test(document.querySelector('#sim-date').textContent)",
    )
    report["after_goto_forward"] = api("/accuracy?city=Delhi")["simulated_today"]
    wait("!document.querySelector('#goto').disabled")
    evaluate(
        "document.querySelector('#goto-date').value='2020-01-04'; document.querySelector('#goto').click()"
    )
    wait(
        "/Jan 4|4 Jan/.test(document.querySelector('#sim-date').textContent) && !document.querySelector('#goto').disabled",
        timeout=60,
    )
    check(
        "Go to an earlier date starts over and plays forward",
        "document.querySelectorAll('#scored tr').length>=3",
    )
    report["after_goto_back"] = api("/accuracy?city=Delhi")
    report["after_goto_back"] = {k: report["after_goto_back"][k] for k in ["simulated_today", "by_city"]}
    evaluate("document.querySelectorAll('.city-score')[1].click()")
    wait("document.querySelector('#chart-city').textContent==='Chennai'")
    check(
        "Choosing a city card switches the chart",
        "document.querySelectorAll('.city-score')[1].getAttribute('aria-pressed')==='true'",
    )
    report["after_advance"] = api("/accuracy?city=Delhi")["by_city"]
    screenshot("accuracy-desktop.png")
    evaluate("document.querySelector('#tab-forecast').click()")
    wait("/Jan 5, 2020|5 Jan 2020/.test(document.querySelector('#fc-date').textContent)")
    check(
        "Forecast tab follows the clock after a jump",
        "/Jan 4|4 Jan/.test(document.querySelector('#sim-date').textContent) && document.querySelectorAll('.fc .fc-aqi').length===3",
    )

    # Operations: graph from dvc dag, links, readable numbers.
    evaluate("document.querySelector('#tab-operations').click()")
    wait("document.querySelector('#dag svg') !== null")
    dag = api("/pipeline")["dag"]
    nodes = sum('["' in line for line in dag.splitlines())
    edges = sum("-->" in line for line in dag.splitlines())
    check(
        "Pipeline graph matches dvc dag",
        f"document.querySelectorAll('#dag g.node').length==={nodes + 1} && document.querySelectorAll('#dag path.edge').length==={edges + 1}",
    )
    check(
        "Graph fits the panel on desktop",
        "document.querySelector('#dag').scrollWidth <= document.querySelector('#dag').clientWidth",
    )
    check("Stage numbers are compact", "!/\\.\\d{2}/.test(document.querySelector('#stages').textContent)")
    check(
        "Tool links are present",
        "['link-grafana','link-mlflow','link-prometheus','link-api'].every(id => document.getElementById(id).href.startsWith('http'))",
    )
    check(
        "Model switch names its target",
        "document.querySelector('#switch-model').textContent.startsWith('Switch to version')",
    )
    check("Seven services listed", "document.querySelectorAll('#services li').length===7")
    check("The clock is hidden on Operations", "document.querySelector('#clockbar').hidden")
    runs = [run for run in api("/pipeline")["runs"] if run["kind"] != "replay"][:8]
    failed = next(run for run in runs if run["status"] == "failed")
    cause = failed["error"].split("\n")[0]
    report["failed_run"] = {"id": failed["id"], "cause": cause}
    check(
        "A failed run shows its root cause first",
        f"[...document.querySelectorAll('#runs details summary')].some(s => s.textContent === {json.dumps(cause)})",
    )
    evaluate(f"document.querySelector('#runs details[data-id=\"{failed['id']}\"]').open = true")
    time.sleep(3.5)
    check(
        "An opened error stays open across the automatic refresh",
        f"document.querySelector('#runs details[data-id=\"{failed['id']}\"]').open",
    )
    screenshot("operations-desktop.png")

    for name in ["forecast", "accuracy", "operations"]:
        viewport(375, mobile=True)
        evaluate(f"document.querySelector('#tab-{name}').click()")
        time.sleep(0.6)
        check(f"{name.title()} fits a 375-pixel phone", "document.documentElement.scrollWidth <= innerWidth")
    evaluate("document.querySelector('#tab-forecast').click()")
    screenshot("forecast-mobile.png")
    evaluate("document.querySelector('#help-open').click()")
    check("Help opens", "document.querySelector('#help').open")
    evaluate("document.querySelector('#help-close').click()")
    check("Help closes", "!document.querySelector('#help').open")
    if errors:
        raise AssertionError("JavaScript exceptions: " + json.dumps(errors)[:500])
    checks.append({"name": "No JavaScript exceptions", "passed": True})
    report["status"] = "passed"
except Exception as exc:
    report["status"] = "failed"
    report["error"] = str(exc)
    raise
finally:
    report["finished_at_unix"] = time.time()
    report["checks"] = checks
    report["passed"] = sum(item["passed"] for item in checks)
    report["failed"] = sum(not item["passed"] for item in checks) + (report.get("status") == "failed")
    (OUTPUT / "browser-e2e.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: report[k] for k in ["status", "passed", "failed"]}))
    if state["ws"]:
        state["ws"].close()
    if chrome:
        chrome.terminate()
        chrome.wait(timeout=10)
