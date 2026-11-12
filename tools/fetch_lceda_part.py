#!/usr/bin/env python3
"""Fetch a part from the LCEDA (JLC/EasyEDA) online library and decrypt its document.

Why this exists
---------------
The offline standard library (``lceda-std.elib``) only carries generic parts; a
SoC such as RK3506 or a DDR3L die lives in the *online* catalogue. The catalogue
search gives metadata only -- the geometry is stored as an encrypted blob whose
key and IV come with the component record. Nothing in this file is a route or a
layout decision; it fetches and decrypts someone else's library document so the
caller can transcribe geometry with its own audited parser.

Chain (measured, do not guess)
-----------------------------
1. ``POST https://pro.lceda.cn/api/v5/devices/search``  {"wd":..., "page":1,
   "pageSize":N}  -- anonymous, no token.
2. ``GET  https://pro.lceda.cn/api/v5/components/<component uuid>``
   -> ``dataStrId`` (url of the ciphertext) + ``key`` (32 hex bytes) +
   ``iv`` (**12 bytes**) + ``docType`` + ``title`` + ``version``.
3. download the ciphertext, decrypt with **AES-256-GCM** (a 12-byte IV rules
   out CBC; the tag sits at the end, so ``decrypt(iv, raw, None)`` -- no
   separate tag), then ``gzip.decompress`` the plaintext.
4. the plaintext is the EasyEDA document: one JSON array per line.

The legacy ``easyeda.com/api/products/<lcsc>/components`` endpoint returns 403
and must not be used.

Copyright
---------
The decrypted document carries the LCEDA copyright header. It is kept as a
*geometry source* for transcription, not redistributed as a library. Callers
must say so in their provenance file, the way
``examples/rk3506_som/library/lceda_source.json`` does.

Usage
-----
    python tools/fetch_lceda_part.py search "MT41K256M16" [--limit 10]
    python tools/fetch_lceda_part.py fetch <component_uuid> --out <dir> \
        [--stem DDR3L]

``fetch`` writes ``<stem>.efp`` (decrypted document), ``<stem>_source.json``
(provenance: uuids, key, iv, title, docType, version, fetched date, copyright
note) and ``<stem>_meta.json`` (the raw component record). Never overwrite an
existing ``.efp`` unless ``--force`` is given.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import gzip
import hashlib
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

BASE = "https://pro.lceda.cn"
COMP_PATH = BASE + "/api/v5/components/"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
HEADERS = {
    "User-Agent": UA,
    "Origin": BASE,
    "Referer": BASE + "/",
    "Accept": "application/json, */*",
}


class FetchError(RuntimeError):
    pass


def _get(url, timeout=30.0):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception as exc:                      # noqa: BLE001 - reported verbatim
        raise FetchError("GET %s failed: %s: %s" % (url, type(exc).__name__, exc))


def search(word, limit=10, page=1):
    """Return the catalogue hits for ``word`` (metadata only, no geometry)."""
    from pcb_agent_mcp import parts
    return parts.search_online(word, limit=limit, page=page)


def component(uuid):
    """Return the raw component record (carries dataStrId, key, iv, docType)."""
    raw = _get(COMP_PATH + str(uuid))
    try:
        out = json.loads(raw.decode("utf-8"))
    except Exception as exc:                      # noqa: BLE001
        raise FetchError("component record is not JSON: %s" % exc)
    if not out.get("success", True):
        raise FetchError("catalogue refused component %s: %s (code=%s)"
                         % (uuid, out.get("message"), out.get("code")))
    data = out.get("result") or out.get("data") or {}
    return data


def _field(data, *names):
    for n in names:
        v = data.get(n)
        if v not in (None, ""):
            return v
    return None


def decrypt(data):
    """AES-256-GCM decrypt of ``dataStrId`` then gunzip. Returns the document text."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except Exception as exc:                      # noqa: BLE001
        raise FetchError("needs the 'cryptography' package: %s" % exc)

    url = _field(data, "dataStrId", "data_str_id", "docUrl")
    key_hex = _field(data, "key", "aesKey")
    iv_hex = _field(data, "iv", "aesIv")
    if not (url and key_hex and iv_hex):
        raise FetchError("component record lacks dataStrId/key/iv: keys=%s"
                         % sorted(data.keys()))
    key = bytes.fromhex(key_hex)
    iv = bytes.fromhex(iv_hex)
    if len(key) != 32:
        raise FetchError("key is %d bytes, AES-256-GCM needs 32" % len(key))
    if len(iv) != 12:
        raise FetchError("iv is %d bytes; 12 means GCM, anything else means this "
                         "file's assumption is wrong" % len(iv))
    raw = _get(url)
    try:
        plain = AESGCM(key).decrypt(iv, raw, None)
    except Exception as exc:                      # noqa: BLE001
        raise FetchError("AES-256-GCM rejected the blob (%s): key/iv or mode "
                         "assumption is wrong" % type(exc).__name__)
    try:
        plain = gzip.decompress(plain)
    except Exception as exc:                      # noqa: BLE001
        raise FetchError("plaintext is not gzip (%s)" % type(exc).__name__)
    return plain.decode("utf-8", "replace"), {"id": _field(data, "id", "uuid"),
                                              "cipher_bytes": len(raw),
                                              "doc_bytes": len(plain)}


def fetch(uuid, outdir, stem="part", force=False):
    data = component(uuid)
    doc, stats = decrypt(data)
    if not doc.lstrip().startswith(("[")):
        raise FetchError("decrypted document does not look like EasyEDA rows "
                         "(first 40 chars: %r)" % doc[:40])
    os.makedirs(outdir, exist_ok=True)
    efp = os.path.join(outdir, stem + ".efp")
    if os.path.exists(efp) and not force:
        raise FetchError("%s exists; pass --force to replace" % efp)
    with open(efp, "w", encoding="utf-8", newline="\n") as f:
        f.write(doc)
    title = _field(data, "title", "display_title") or ""
    copyright_ = None
    for line in doc.splitlines()[:8]:
        if "版权" in line or "Copyright" in line:
            copyright_ = line[:400]
            break
    src = {
        "component_uuid": uuid,
        "device_uuid": _field(data, "deviceUuid", "device_uuid"),
        "footprint_uuid": _field(data, "footprintUuid", "footprint_uuid"),
        "symbol_uuid": _field(data, "symbolUuid", "symbol_uuid"),
        "lcsc": _field(data, "product_code", "lcsc", "supplierPart"),
        "doc_title": title,
        "doc_doctype": _field(data, "docType", "doc_type"),
        "doc_version": _field(data, "version"),
        "dataStrId": _field(data, "dataStrId", "data_str_id"),
        "aes_key_hex": _field(data, "key", "aesKey"),
        "aes_iv_hex": _field(data, "iv", "aesIv"),
        "fetched": _dt.date.today().isoformat(),
        "cipher_bytes": stats["cipher_bytes"],
        "doc_bytes": stats["doc_bytes"],
        "doc_sha256": hashlib.sha256(doc.encode("utf-8")).hexdigest(),
        "copyright": copyright_,
        "note": ("Geometry source for transcription only. The document is the "
                 "LCEDA/EasyEDA official library's; do not redistribute it as a "
                 "library and do not treat its declared CANVAS unit as truth "
                 "(solve the scale against a datasheet dimension instead)."),
    }
    with open(os.path.join(outdir, stem + "_source.json"), "w", encoding="utf-8",
              newline="\n") as f:
        json.dump(src, f, ensure_ascii=False, indent=2)
        f.write("\n")
    with open(os.path.join(outdir, stem + "_meta.json"), "w", encoding="utf-8",
              newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    return {"efp": efp, "rows": doc.count("\n") + 1, "title": title,
            "doc_bytes": stats["doc_bytes"], "copyright": bool(copyright_)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=["search", "fetch"])
    ap.add_argument("target", help="search word, or component uuid")
    ap.add_argument("--out", default=None, help="output dir for fetch")
    ap.add_argument("--stem", default="part", help="file stem for fetch")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.action == "search":
            hits = search(a.target, limit=a.limit)
            print(json.dumps(hits, ensure_ascii=False, indent=2))
            return 0
        if not a.out:
            print("fetch needs --out", file=sys.stderr)
            return 2
        out = fetch(a.target, a.out, stem=a.stem, force=a.force)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    except FetchError as exc:
        print("FETCH_FAILED: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
