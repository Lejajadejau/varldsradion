#!/usr/bin/env python3
"""Hämtar avsnitt från källorna i poddar/kallor.txt och skriver poddar/avsnitt.json.

Körs av GitHub Actions. Använder bara Pythons standardbibliotek.
"""
import email.utils
import html
import json
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROT = Path(__file__).resolve().parents[2]
KALLOR = ROT / "poddar" / "kallor.txt"
UT = ROT / "poddar" / "avsnitt.json"
PER_KALLA = 25
UA = "Varldspodden/1.0 (+https://github.com/Lejajadejau/varldsradion)"

NS = {
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "media": "http://search.yahoo.com/mrss/",
}


def hamta(url, tidsgrans=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=tidsgrans) as r:
        return r.read()


def itunes(params):
    url = "https://itunes.apple.com/" + params
    return json.loads(hamta(url).decode("utf-8")).get("results", [])


def los_upp(rad):
    """Gör om en rad i kallor.txt till en RSS-adress."""
    rad = rad.strip()
    m = re.match(r"^s[öo]k\s*:\s*(.+)$", rad, re.I)
    if m:
        term = urllib.parse.quote(m.group(1).strip())
        for land in ("se", "us"):
            res = itunes(f"search?media=podcast&entity=podcast&limit=1&country={land}&term={term}")
            if res and res[0].get("feedUrl"):
                return res[0]["feedUrl"]
        raise ValueError("ingen träff i Apples poddkatalog")
    m = re.search(r"podcasts\.apple\.com/.*?id(\d+)", rad)
    if m:
        res = itunes(f"lookup?id={m.group(1)}&entity=podcast")
        if res and res[0].get("feedUrl"):
            return res[0]["feedUrl"]
        raise ValueError("Apple-länken saknar RSS-flöde")
    if re.match(r"^https?://", rad):
        return rad
    raise ValueError("okänt format")


def text(el):
    return (el.text or "").strip() if el is not None else ""


def rensa(s, max_len=320):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(re.sub(r"\s+", " ", s)).strip()
    return (s[: max_len - 1].rstrip() + "…") if len(s) > max_len else s


def datum(s):
    if not s:
        return None
    try:
        d = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat()


def langd(s):
    s = (s or "").strip()
    if not s:
        return None
    try:
        if ":" in s:
            sek = 0
            for del_ in s.split(":"):
                sek = sek * 60 + int(float(del_))
            return sek
        return int(float(s))
    except ValueError:
        return None


def tolka(xml_bytes, kalla_id):
    rot = ET.fromstring(xml_bytes)
    avsnitt = []
    if rot.tag.endswith("rss") or rot.find("channel") is not None:
        kanal = rot.find("channel")
        bild = kanal.find("itunes:image", NS)
        bild = bild.get("href") if bild is not None else text(kanal.find("image/url"))
        info = {
            "titel": text(kanal.find("title")),
            "lank": text(kanal.find("link")),
            "bild": bild or "",
            "beskrivning": rensa(text(kanal.find("description")), 200),
        }
        for it in kanal.findall("item")[:PER_KALLA]:
            enc = it.find("enclosure")
            ljud = enc.get("url") if enc is not None else ""
            typ = enc.get("type", "") if enc is not None else ""
            ib = it.find("itunes:image", NS)
            beskr = text(it.find("itunes:summary", NS)) or text(it.find("description")) or text(it.find("content:encoded", NS))
            avsnitt.append({
                "kalla": kalla_id,
                "id": text(it.find("guid")) or ljud or text(it.find("link")),
                "titel": rensa(text(it.find("title")), 200),
                "datum": datum(text(it.find("pubDate"))),
                "ljud": ljud if (typ.startswith("audio") or typ.startswith("video") or re.search(r"\.(mp3|m4a|aac|ogg|opus)(\?|$)", ljud, re.I)) else "",
                "langd": langd(text(it.find("itunes:duration", NS))),
                "lank": text(it.find("link")),
                "bild": ib.get("href") if ib is not None else "",
                "text": rensa(beskr),
            })
    else:  # Atom
        a = "{http://www.w3.org/2005/Atom}"
        lank = rot.find(f"{a}link[@rel='alternate']")
        if lank is None:
            lank = rot.find(f"{a}link")
        info = {
            "titel": text(rot.find(f"{a}title")),
            "lank": lank.get("href", "") if lank is not None else "",
            "bild": text(rot.find(f"{a}logo")) or text(rot.find(f"{a}icon")),
            "beskrivning": rensa(text(rot.find(f"{a}subtitle")), 200),
        }
        for e in rot.findall(f"{a}entry")[:PER_KALLA]:
            ljud, lank = "", ""
            for l in e.findall(f"{a}link"):
                if l.get("rel") == "enclosure" and (l.get("type", "").startswith("audio")):
                    ljud = l.get("href", "")
                elif l.get("rel", "alternate") == "alternate":
                    lank = l.get("href", "")
            avsnitt.append({
                "kalla": kalla_id,
                "id": text(e.find(f"{a}id")) or lank,
                "titel": rensa(text(e.find(f"{a}title")), 200),
                "datum": datum(text(e.find(f"{a}published")) or text(e.find(f"{a}updated"))),
                "ljud": ljud,
                "langd": None,
                "lank": lank,
                "bild": "",
                "text": rensa(text(e.find(f"{a}summary")) or text(e.find(f"{a}content"))),
            })
    return info, avsnitt


def main():
    tidigare = {}
    if UT.exists():
        try:
            for k in json.loads(UT.read_text("utf-8")).get("kallor", []):
                tidigare[k["rad"]] = k
        except (ValueError, KeyError):
            pass

    rader = [r.strip() for r in KALLOR.read_text("utf-8").splitlines()]
    rader = [r for r in rader if r and not r.startswith("#")]

    kallor, alla = [], []
    for nr, rad in enumerate(rader):
        kid = f"k{nr}"
        post = {"id": kid, "rad": rad, "fel": ""}
        try:
            flode = tidigare.get(rad, {}).get("flode") or los_upp(rad)
            info, avsnitt = tolka(hamta(flode), kid)
            post.update(info, flode=flode)
            alla.extend(avsnitt)
            print(f"OK   {rad} → {info['titel']} ({len(avsnitt)} avsnitt)")
        except Exception as e:  # en trasig källa ska inte stoppa resten
            gammal = tidigare.get(rad)
            if gammal:
                post.update({k: gammal.get(k, "") for k in ("titel", "lank", "bild", "beskrivning", "flode")})
            post["fel"] = str(e)[:200]
            print(f"FEL  {rad}: {e}", file=sys.stderr)
        kallor.append(post)

    alla.sort(key=lambda x: x["datum"] or "", reverse=True)

    # Skriv bara om något faktiskt ändrats, så att repot inte fylls av tomma uppdateringar
    if UT.exists():
        try:
            gammal = json.loads(UT.read_text("utf-8"))
            if gammal.get("kallor") == kallor and gammal.get("avsnitt") == alla:
                print("Inga ändringar.")
                return
        except ValueError:
            pass

    UT.write_text(json.dumps({
        "uppdaterad": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kallor": kallor,
        "avsnitt": alla,
    }, ensure_ascii=False, separators=(",", ":")), "utf-8")


if __name__ == "__main__":
    main()
