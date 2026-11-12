"""Port metadata embedded reversibly in existing shipping_details TEXT.

No source schema migration. Only an exact, versioned metadata block is parsed.
Legacy free text remains untouched; unknown content is preserved.
"""
import re

START = "[PORTEXA_PORTS_V1]"
END = "[/PORTEXA_PORTS_V1]"
BLOCK = re.compile(r"(?:^|\n)" + re.escape(START) + r"\n(.*?)\n" + re.escape(END) + r"(?=\n|$)", re.S)
PORT = re.compile(r"[^\r\n\x00-\x1f]{0,120}\Z")


def split_details(raw):
    text = raw or ""
    matches = list(BLOCK.finditer(text))
    if len(matches) > 1:
        raise ValueError("Duplicate port metadata blocks")
    if not matches:
        return {"loading_port": "", "destination_port": "", "details": text}
    match = matches[0]
    lines = match.group(1).split("\n")
    if len(lines) != 2 or not lines[0].startswith("loading_port=") or not lines[1].startswith("destination_port="):
        raise ValueError("Unrecognized port metadata block")
    loading = lines[0][len("loading_port="):]
    destination = lines[1][len("destination_port="):]
    if not PORT.fullmatch(loading) or not PORT.fullmatch(destination):
        raise ValueError("Invalid stored port metadata")
    details = text[:match.start()] + text[match.end():]
    return {"loading_port": loading, "destination_port": destination, "details": details.strip("\n")}


def combine_details(details, loading_port, destination_port):
    if not isinstance(details, str) or not isinstance(loading_port, str) or not isinstance(destination_port, str):
        raise ValueError("Invalid port form")
    loading_port, destination_port = loading_port.strip(), destination_port.strip()
    for port in (loading_port, destination_port):
        if not PORT.fullmatch(port):
            raise ValueError("Port must be at most 120 characters without line breaks")
    if START in details or END in details:
        raise ValueError("Reserved port metadata marker found in details")
    block = (START + "\nloading_port=" + loading_port +
             "\ndestination_port=" + destination_port + "\n" + END)
    result = details + ("\n" if details and (loading_port or destination_port) else "")
    if loading_port or destination_port:
        result += block
    if len(result) > 2000:
        raise ValueError("Shipping details exceed 2000 characters")
    return result or None
