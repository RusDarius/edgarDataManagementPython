import os
import requests
from arelle import Cntlr

IXBRL_URL = "https://www.sec.gov/Archives/edgar/data/1048911/000095017023048994/fdx-20230831.htm"
LOCAL_FILE = "fdx-20230831.htm"
TARGET_TAGS = {
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
    "Revenues",
}


# === Download Filing ===
def download_if_needed(url, filename):
    if os.path.exists(filename):
        print("Already downloaded.")
        return
    print(f"Downloading: {url}")
    headers = {"User-Agent": "Your Name Contact@Email.com"}
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    with open(filename, "wb") as f:
        f.write(resp.content)
    print(f"Saved to {filename}")


# === Load iXBRL ===
def load_xbrl(file_path):
    print("Parsing with Arelle (inline=True)")
    cntlr = Cntlr.Cntlr(logFileName="logToPrint")
    model = cntlr.modelManager.load(
        file_path, mappedUri=None, base=None, isInlineXbrl=True
    )
    if not model or not model.facts:
        raise RuntimeError("Failed to load or no facts found.")
    return model


# === Extract Facts ===
def extract_target_facts(model):
    print("\n=== Extracted Facts ===")
    for fact in model.facts:
        if not fact.concept:
            continue
        if fact.concept.name in TARGET_TAGS:
            print("----")
            print("Concept:", fact.concept.name)
            print("Value:", fact.value)
            print("ContextRef:", fact.contextID)

            ctx = fact.context
            if ctx is not None and ctx.segDimValues:
                for dim, mem in ctx.segDimValues.items():
                    print(
                        f" - Dimension: {dim.qname.localName}, Member: {mem.qname.localName}"
                    )


# === Run ===
if __name__ == "__main__":
    download_if_needed(IXBRL_URL, LOCAL_FILE)
    model = load_xbrl(LOCAL_FILE)
    extract_target_facts(model)
