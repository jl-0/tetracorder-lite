"""specpr binary format reader and writer.

specpr libraries are flat files of fixed 1536-byte, big-endian records.  Record 0
is an ASCII label; data starts at record 1.  Each record's type is selected by the
low two bits of the first integer (``icflag``):

    icflag & 3 == 0   data header    (256 channels of float data + full header)
    icflag & 3 == 1   data continuation (383 channels of float data)
    icflag & 3 == 2   text header
    icflag & 3 == 3   text continuation

A spectrum longer than 256 channels spills into continuation records: the header
holds the first 256 values, each continuation record the next 383.  ``itchan`` in
the header gives the total channel count.

Format spec: ``tetracorder/specpr/specpr-format-2,3/specpr-format-v2.txt``.  Field
offsets below were cross-checked against ``splib06b`` and the shipped ``s06emitc``.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

RECORD_BYTES = 1536
HEAD_CHANNELS = 256          # float channels carried in a data-header record
CONT_CHANNELS = 383          # float channels carried in a data-continuation record
DATA_OFFSET = 512            # byte offset of the float data array in a header record
DELETED = -1.23e34           # specpr "deleted point" sentinel

# --- data-header field byte offsets (within a 1536-byte record) ---------------
OFF_ICFLAG = 0               # int32   32 bit flags
OFF_ITITL = 4                # char*40 title
OFF_USERNM = 44              # char*8  user name
OFF_ITCHAN = 80              # int32   total channel count
OFF_IRWAV = 100              # int32   record # holding the wavelengths
OFF_IRESPT = 104             # int32   record # holding the resolution / FWHM
OFF_IRECNO = 108             # int32   this record's own number
OFF_ITPNTR = 112             # int32   text-data record pointer
OFF_IHIST = 116              # char*60 automatic history
OFF_MHIST = 176              # char*296 manual history

# --- text-header field byte offsets (format spec case 3) ----------------------
OFF_TXT_ITITL = 4            # char*40 title
OFF_TXT_USERNM = 44          # char*8  user name
OFF_TXT_ITXTPT = 52          # int32   text-data record pointer
OFF_TXT_ITXTCH = 56          # int32   number of text characters
OFF_TXT_ITEXT = 60           # char*   text body

TEXT_FIELD_LEN = RECORD_BYTES - OFF_TXT_ITEXT   # 1476 bytes of itext (spec case 3)

# The shipped libraries pad every spectrum with four identical text records.
# ``itxtch`` counts only the leading line; the itext field itself carries the line
# plus a trailing newline, space-filled to its full 1476-byte width.
PAD_LINE = "Dummy text record for future expansion. \n"   # 41 chars -> itxtch
PAD_ITEXT = (PAD_LINE + "\n").ljust(TEXT_FIELD_LEN, " ")


def record_type(icflag: int) -> int:
    """Return the record type (0..3) encoded in the low two bits of ``icflag``."""
    return icflag & 3


@dataclass
class Record:
    """A single decoded 1536-byte specpr record (header fields, raw bytes kept)."""

    raw: bytes
    icflag: int
    rtype: int

    @property
    def title(self) -> str:
        return self.raw[OFF_ITITL:OFF_ITITL + 40].decode("latin1")

    @property
    def itchan(self) -> int:
        return struct.unpack_from(">i", self.raw, OFF_ITCHAN)[0]

    @property
    def irwav(self) -> int:
        return struct.unpack_from(">i", self.raw, OFF_IRWAV)[0]

    @property
    def irespt(self) -> int:
        return struct.unpack_from(">i", self.raw, OFF_IRESPT)[0]

    @property
    def irecno(self) -> int:
        return struct.unpack_from(">i", self.raw, OFF_IRECNO)[0]


class SpecprFile:
    """Read-only view over a specpr library file, addressed by record number."""

    def __init__(self, data: bytes):
        self.data = data
        self.nrecords = len(data) // RECORD_BYTES

    @classmethod
    def open(cls, path) -> "SpecprFile":
        with open(path, "rb") as fh:
            return cls(fh.read())

    def record(self, i: int) -> Record:
        buf = self.data[i * RECORD_BYTES:(i + 1) * RECORD_BYTES]
        if len(buf) != RECORD_BYTES:
            raise IndexError(f"record {i} out of range (nrecords={self.nrecords})")
        icflag = struct.unpack_from(">i", buf, OFF_ICFLAG)[0]
        return Record(raw=buf, icflag=icflag, rtype=record_type(icflag))

    def has_record(self, i: int) -> bool:
        return 0 <= i < self.nrecords

    def read_spectrum(self, recno: int) -> np.ndarray:
        """Reassemble the full float array of a spectrum starting at ``recno``.

        Reads the header's 256 channels, then successive continuation records
        (383 channels each) until ``itchan`` values have been collected.
        """
        head = self.record(recno)
        if head.rtype != 0:
            raise ValueError(f"record {recno} is not a data header (type {head.rtype})")
        n = head.itchan
        values = list(_floats(head.raw, DATA_OFFSET, min(n, HEAD_CHANNELS)))
        i = recno + 1
        while len(values) < n:
            cont = self.record(i)
            take = min(CONT_CHANNELS, n - len(values))
            values.extend(_floats(cont.raw, 4, take))
            i += 1
        return np.asarray(values, dtype=np.float32)


def _floats(buf: bytes, offset: int, count: int):
    return struct.unpack_from(f">{count}f", buf, offset)


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

def _pack_int(buf: bytearray, offset: int, value: int) -> None:
    struct.pack_into(">i", buf, offset, value)


def _pack_text(buf: bytearray, offset: int, text: str, length: int) -> None:
    encoded = text.encode("latin1", "replace")[:length].ljust(length, b" ")
    buf[offset:offset + length] = encoded


def label_record(text: str) -> bytes:
    """Build record 0, the ASCII label record.

    Matches the shipped libraries: CRLF-terminated key/value lines, then NUL fill.
    """
    body = text.encode("latin1")
    buf = bytearray(b"\x00" * RECORD_BYTES)
    buf[0:len(body)] = body
    return bytes(buf)


def default_label() -> bytes:
    return label_record("SPECPR_FS=2.0\r\nRECORD_BYTES=1536\r\nLABEL_RECORDS=1\r\n")


def _data_records(icflag_head: int, title: str, usernm: str, itchan: int,
                  irwav: int, irespt: int, irecno: int, values: np.ndarray,
                  ihist: str = "", mhist: str = "",
                  template: Record | None = None) -> list[bytes]:
    """Encode a spectrum as one header record + N continuation records.

    When ``template`` is given, its header bytes are copied first (preserving the
    time/date/geometry fields and other metadata exactly as specpr would), then
    the fields we control are overwritten.
    """
    if template is not None:
        head = bytearray(template.raw)
    else:
        head = bytearray(b"\x00" * RECORD_BYTES)

    _pack_int(head, OFF_ICFLAG, icflag_head)
    _pack_text(head, OFF_ITITL, title, 40)
    _pack_text(head, OFF_USERNM, usernm, 8)
    _pack_int(head, OFF_ITCHAN, itchan)
    _pack_int(head, OFF_IRWAV, irwav)
    _pack_int(head, OFF_IRESPT, irespt)
    _pack_int(head, OFF_IRECNO, irecno)
    _pack_int(head, OFF_ITPNTR, 0)
    _pack_text(head, OFF_IHIST, ihist, 60)
    _pack_text(head, OFF_MHIST, mhist, 296)

    vals = np.asarray(values, dtype=np.float32)
    if vals.size != itchan:
        raise ValueError(f"expected {itchan} values, got {vals.size}")

    # header carries the first 256 channels
    head_vals = vals[:HEAD_CHANNELS]
    struct.pack_into(f">{head_vals.size}f", head, DATA_OFFSET, *head_vals.tolist())
    records = [bytes(head)]

    # continuation records carry the rest, 383 channels each
    idx = HEAD_CHANNELS
    cont_icflag = (icflag_head & ~3) | 1   # same high flags, type 1
    while idx < itchan:
        chunk = vals[idx:idx + CONT_CHANNELS]
        cont = bytearray(b"\x00" * RECORD_BYTES)
        _pack_int(cont, OFF_ICFLAG, cont_icflag)
        struct.pack_into(f">{chunk.size}f", cont, 4, *chunk.tolist())
        records.append(bytes(cont))
        idx += CONT_CHANNELS
    return records


def text_pad_record(title: str = "..") -> bytes:
    """A text-header record used as inter-spectrum padding (``[sppad]``).

    Reproduces the shipped libraries' padding record byte-for-byte: a type-2 text
    header with title ``..`` and the standard "Dummy text record for future
    expansion." payload (``itxtch`` counts the leading line; the full 1476-byte
    itext field carries that line plus a newline, space-filled).
    """
    buf = bytearray(b"\x00" * RECORD_BYTES)
    _pack_int(buf, OFF_ICFLAG, 2)          # type 2, text header
    _pack_text(buf, OFF_TXT_ITITL, title, 40)
    _pack_int(buf, OFF_TXT_ITXTPT, 0)
    _pack_int(buf, OFF_TXT_ITXTCH, len(PAD_LINE))
    encoded = PAD_ITEXT.encode("latin1", "replace")[:TEXT_FIELD_LEN]
    buf[OFF_TXT_ITEXT:OFF_TXT_ITEXT + len(encoded)] = encoded
    return bytes(buf)


class SpecprWriter:
    """Accumulates records and tracks the next record number written.

    Record numbering is significant: Tetracorder fit-scripts reference the library
    by absolute record number, so callers must emit records in order and rely on
    :attr:`next_recno` to place headers at their expected slots.
    """

    def __init__(self, label: bytes | None = None):
        self.records: list[bytes] = [label if label is not None else default_label()]

    @property
    def next_recno(self) -> int:
        """Record number that the next appended record will occupy."""
        return len(self.records)

    def append(self, record: bytes) -> int:
        recno = len(self.records)
        self.records.append(record)
        return recno

    def append_spectrum(self, *, icflag_head: int, title: str, usernm: str,
                        itchan: int, irwav: int, irespt: int,
                        values: np.ndarray, ihist: str = "", mhist: str = "",
                        template: Record | None = None) -> int:
        """Append a spectrum's header + continuation records; return the head recno."""
        recno = len(self.records)
        recs = _data_records(icflag_head, title, usernm, itchan, irwav, irespt,
                             recno, values, ihist, mhist, template)
        self.records.extend(recs)
        return recno

    def append_pads(self, count: int, title: str = "..") -> None:
        for _ in range(count):
            self.records.append(text_pad_record(title))

    def to_bytes(self) -> bytes:
        return b"".join(self.records)

    def write(self, path) -> None:
        with open(path, "wb") as fh:
            fh.write(self.to_bytes())
