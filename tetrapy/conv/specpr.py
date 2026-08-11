"""
Read and write specpr spectral-library binaries.

A specpr library is a flat file of fixed 1536-byte, big-endian records. Record 0
is an ASCII label; spectra start at record 1. The low two bits of a record's first
integer (``icflag``) select its type:

- ``0`` data header: full header + the first 256 float channels.
- ``1`` data continuation: 383 more float channels.
- ``2`` text header, ``3`` text continuation.

A spectrum longer than 256 channels spills into continuation records; ``itchan`` in
the header gives the total channel count. Absolute record numbers are meaningful --
Tetracorder's fit scripts reference the convolved library by record number -- so the
:class:`SpecprWriter` preserves ordering and exposes the next record slot.

Field offsets follow ``tetracorder/specpr/specpr-format-2,3/specpr-format-v2.txt``.
"""

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional, Union

import numpy as np

RECORD_BYTES = 1536
HEAD_CHANNELS = 256          # float channels stored in a data-header record
CONT_CHANNELS = 383          # float channels stored in a data-continuation record
DATA_OFFSET = 512            # byte offset of the float data in a header record
DELETED = -1.23e34           # specpr "deleted point" sentinel

# Data-header field byte offsets within a 1536-byte record.
OFF_ICFLAG = 0               # int32  32-bit flags (type = low two bits)
OFF_ITITL = 4                # char*40 title
OFF_USERNM = 44              # char*8  user name
OFF_ITCHAN = 80              # int32  total channel count
OFF_IRWAV = 100              # int32  record holding the wavelengths
OFF_IRESPT = 104             # int32  record holding the resolution / FWHM
OFF_IRECNO = 108             # int32  this record's own number
OFF_ITPNTR = 112             # int32  text-data record pointer
OFF_IHIST = 116              # char*60 automatic history
OFF_MHIST = 176              # char*296 manual history


@dataclass
class Record:
    """A single decoded 1536-byte record, keeping its raw bytes."""

    raw: bytes

    @property
    def icflag(self) -> int:
        return struct.unpack_from(">i", self.raw, OFF_ICFLAG)[0]

    @property
    def rtype(self) -> int:
        return self.icflag & 3

    @property
    def title(self) -> str:
        return self.raw[OFF_ITITL:OFF_ITITL + 40].decode("latin1").rstrip()

    @property
    def itchan(self) -> int:
        return struct.unpack_from(">i", self.raw, OFF_ITCHAN)[0]

    @property
    def irwav(self) -> int:
        return struct.unpack_from(">i", self.raw, OFF_IRWAV)[0]

    @property
    def irespt(self) -> int:
        return struct.unpack_from(">i", self.raw, OFF_IRESPT)[0]


class SpecprFile:
    """Read-only view over a specpr library, addressed by record number."""

    def __init__(self, data: bytes):
        self.data = data
        self.nrecords = len(data) // RECORD_BYTES

    @classmethod
    def open(cls, path: Union[str, Path]) -> "SpecprFile":
        return cls(Path(path).read_bytes())

    def record(self, i: int) -> Record:
        """Return the decoded record at index ``i``."""
        buf = self.data[i * RECORD_BYTES:(i + 1) * RECORD_BYTES]
        if len(buf) != RECORD_BYTES:
            raise IndexError(f"record {i} out of range (nrecords={self.nrecords})")
        return Record(raw=buf)

    def spectra(self) -> Iterator[int]:
        """Yield the record number of every data-header (a spectrum start)."""
        for i in range(1, self.nrecords):
            if self.record(i).rtype == 0:
                yield i

    def read_spectrum(self, recno: int) -> np.ndarray:
        """
        Reassemble the full float array of the spectrum starting at ``recno``.

        Reads the header's channels, then successive continuation records until
        ``itchan`` values have been collected.
        """
        head = self.record(recno)
        if head.rtype != 0:
            raise ValueError(f"record {recno} is not a data header (type {head.rtype})")

        n = head.itchan
        values = list(_floats(head.raw, DATA_OFFSET, min(n, HEAD_CHANNELS)))

        i = recno + 1
        while len(values) < n:
            cont = self.record(i)
            values.extend(_floats(cont.raw, 4, min(CONT_CHANNELS, n - len(values))))
            i += 1

        return np.asarray(values, dtype=np.float32)


def _floats(buf: bytes, offset: int, count: int) -> tuple:
    return struct.unpack_from(f">{count}f", buf, offset)


class SpecprWriter:
    """
    Accumulate records for a new specpr library, tracking the next record slot.

    Callers append records in order; :attr:`next_recno` reports where the next
    record will land so headers can be placed at their expected absolute slots.
    """

    def __init__(self, label: Optional[bytes] = None):
        self.records: List[bytes] = [label if label is not None else _label_record()]

    @property
    def next_recno(self) -> int:
        """Record number the next appended record will occupy."""
        return len(self.records)

    def append(self, record: bytes) -> int:
        self.records.append(record)
        return len(self.records) - 1

    def append_pads(self, count: int) -> None:
        """Append ``count`` text-header padding records (the shipped ``..`` spacer)."""
        for _ in range(count):
            self.records.append(_text_record(".."))

    def append_spectrum(
        self,
        values: np.ndarray,
        title: str,
        itchan: int,
        icflag: int = 16,
        irwav: int = 0,
        irespt: int = 0,
        usernm: str = "tetracnv",
        template: Optional[Record] = None,
    ) -> int:
        """
        Append a spectrum as one header record plus continuation records.

        When ``template`` is given its header bytes are copied first (preserving the
        date/geometry metadata specpr would otherwise carry), then the fields we
        control are overwritten. Returns the header's record number.
        """
        recno = len(self.records)
        head = bytearray(template.raw if template is not None else bytes(RECORD_BYTES))

        _pack_int(head, OFF_ICFLAG, (icflag & ~3))  # data header: low two bits = 0
        _pack_text(head, OFF_ITITL, title, 40)
        _pack_text(head, OFF_USERNM, usernm, 8)
        _pack_int(head, OFF_ITCHAN, itchan)
        _pack_int(head, OFF_IRWAV, irwav)
        _pack_int(head, OFF_IRESPT, irespt)
        _pack_int(head, OFF_IRECNO, recno)
        _pack_int(head, OFF_ITPNTR, 0)

        vals = np.asarray(values, dtype=np.float32)
        if vals.size != itchan:
            raise ValueError(f"expected {itchan} values, got {vals.size}")

        head_vals = vals[:HEAD_CHANNELS]
        struct.pack_into(f">{head_vals.size}f", head, DATA_OFFSET, *head_vals.tolist())
        self.records.append(bytes(head))

        cont_icflag = (icflag & ~3) | 1  # continuation: low two bits = 1
        for start in range(HEAD_CHANNELS, itchan, CONT_CHANNELS):
            chunk = vals[start:start + CONT_CHANNELS]
            cont = bytearray(RECORD_BYTES)
            _pack_int(cont, OFF_ICFLAG, cont_icflag)
            struct.pack_into(f">{chunk.size}f", cont, 4, *chunk.tolist())
            self.records.append(bytes(cont))

        return recno

    def write(self, path: Union[str, Path]) -> None:
        Path(path).write_bytes(b"".join(self.records))


def _pack_int(buf: bytearray, offset: int, value: int) -> None:
    struct.pack_into(">i", buf, offset, value)


def _pack_text(buf: bytearray, offset: int, text: str, length: int) -> None:
    buf[offset:offset + length] = text.encode("latin1", "replace")[:length].ljust(length, b" ")


def _label_record() -> bytes:
    """Build record 0, the ASCII label the specpr format expects."""
    body = b"SPECPR_FS=2.0\r\nRECORD_BYTES=1536\r\nLABEL_RECORDS=1\r\n"
    buf = bytearray(RECORD_BYTES)
    buf[0:len(body)] = body
    return bytes(buf)


def _text_record(title: str) -> bytes:
    """Build a type-2 text-header record carrying ``title`` (used for padding)."""
    buf = bytearray(RECORD_BYTES)
    _pack_int(buf, OFF_ICFLAG, 2)
    _pack_text(buf, OFF_ITITL, title, 40)
    return bytes(buf)
