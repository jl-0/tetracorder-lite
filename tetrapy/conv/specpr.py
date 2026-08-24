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
    """
    A single decoded 1536-byte specpr record with lazy field access.

    This class wraps the raw bytes of a specpr record and provides property
    accessors to extract header fields on demand without unpacking the entire
    record upfront. The low two bits of ``icflag`` encode the record type.

    Attributes
    ----------
    raw : bytes
        The full 1536-byte record data in big-endian format.
    """

    raw: bytes

    @property
    def icflag(self) -> int:
        """
        The 32-bit flags integer from the record header.

        Returns
        -------
        int
            Flags value; the low two bits encode the record type.
        """
        return struct.unpack_from(">i", self.raw, OFF_ICFLAG)[0]

    @property
    def rtype(self) -> int:
        """
        The record type extracted from the low two bits of icflag.

        Returns
        -------
        int
            Record type: 0 = data header, 1 = data continuation,
            2 = text header, 3 = text continuation.
        """
        return self.icflag & 3

    @property
    def title(self) -> str:
        """
        The 40-character title field, decoded and stripped of trailing spaces.

        Returns
        -------
        str
            Record title from the header.
        """
        return self.raw[OFF_ITITL:OFF_ITITL + 40].decode("latin1").rstrip()

    @property
    def itchan(self) -> int:
        """
        The total channel count for this spectrum (data header only).

        Returns
        -------
        int
            Number of channels in the complete spectrum, including continuation records.
        """
        return struct.unpack_from(">i", self.raw, OFF_ITCHAN)[0]

    @property
    def irwav(self) -> int:
        """
        The record number holding the wavelength grid for this spectrum.

        Returns
        -------
        int
            Wavelength record pointer.
        """
        return struct.unpack_from(">i", self.raw, OFF_IRWAV)[0]

    @property
    def irespt(self) -> int:
        """
        The record number holding the resolution/FWHM grid for this spectrum.

        Returns
        -------
        int
            Resolution record pointer.
        """
        return struct.unpack_from(">i", self.raw, OFF_IRESPT)[0]


class SpecprFile:
    """
    Read-only view over a specpr library, addressed by record number.

    This class provides random access to records in a specpr binary library.
    Record 0 is the ASCII label; data records start at 1. Spectra longer than
    256 channels span multiple records (header + continuations).

    Attributes
    ----------
    data : bytes
        The complete library file contents.
    nrecords : int
        Total number of 1536-byte records in the file.
    """

    def __init__(self, data: bytes) -> None:
        """
        Initialize a SpecprFile from raw library bytes.

        Parameters
        ----------
        data : bytes
            The complete specpr library file contents.
        """
        self.data = data
        self.nrecords = len(data) // RECORD_BYTES

    @classmethod
    def open(cls, path: Union[str, Path]) -> "SpecprFile":
        """
        Open a specpr library from a file path.

        Parameters
        ----------
        path : str or Path
            Path to the specpr library file.

        Returns
        -------
        SpecprFile
            An opened library ready for record access.
        """
        return cls(Path(path).read_bytes())

    def record(self, i: int) -> Record:
        """
        Return the decoded record at index ``i``.

        Parameters
        ----------
        i : int
            Record number (0-indexed).

        Returns
        -------
        Record
            The decoded record with lazy field access.

        Raises
        ------
        IndexError
            If ``i`` is out of range.
        """
        buf = self.data[i * RECORD_BYTES:(i + 1) * RECORD_BYTES]
        if len(buf) != RECORD_BYTES:
            raise IndexError(f"record {i} out of range (nrecords={self.nrecords})")
        return Record(raw=buf)

    def spectra(self) -> Iterator[int]:
        """
        Yield the record number of every data-header (a spectrum start).

        Yields
        ------
        int
            Record numbers of all data headers (type 0) in the library,
            starting from record 1.
        """
        for i in range(1, self.nrecords):
            if self.record(i).rtype == 0:
                yield i

    def read_spectrum(self, recno: int) -> np.ndarray:
        """
        Reassemble the full float array of the spectrum starting at ``recno``.

        Reads the header's channels, then successive continuation records until
        ``itchan`` values have been collected.

        Parameters
        ----------
        recno : int
            Record number of the data header (type 0).

        Returns
        -------
        numpy.ndarray
            The complete spectrum as float32, length ``itchan``.

        Raises
        ------
        ValueError
            If ``recno`` does not point to a data header.
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
    """
    Unpack ``count`` big-endian floats from a byte buffer.

    Parameters
    ----------
    buf : bytes
        Source buffer.
    offset : int
        Byte offset to start unpacking from.
    count : int
        Number of float32 values to unpack.

    Returns
    -------
    tuple
        Tuple of ``count`` float values.
    """
    return struct.unpack_from(f">{count}f", buf, offset)


class SpecprWriter:
    """
    Accumulate records for a new specpr library, tracking the next record slot.

    This class builds a specpr library in memory by appending records in order.
    The :attr:`next_recno` property reports where the next record will land,
    allowing callers to place headers at expected absolute record numbers that
    downstream tools (like tetracorder) reference directly.

    Attributes
    ----------
    records : list[bytes]
        The accumulated list of 1536-byte records, starting with the ASCII label.
    """

    def __init__(self, label: Optional[bytes] = None) -> None:
        """
        Initialize a new specpr library writer with a label record.

        Parameters
        ----------
        label : bytes, optional
            Custom ASCII label record. If None, a standard specpr v2 label is used.
        """
        self.records: List[bytes] = [label if label is not None else _label_record()]

    @property
    def next_recno(self) -> int:
        """
        Record number the next appended record will occupy.

        Returns
        -------
        int
            The 0-indexed position of the next record to be added.
        """
        return len(self.records)

    def append(self, record: bytes) -> int:
        """
        Append a raw 1536-byte record to the library.

        Parameters
        ----------
        record : bytes
            Complete 1536-byte record.

        Returns
        -------
        int
            The record number where this record was placed.
        """
        self.records.append(record)
        return len(self.records) - 1

    def append_pads(self, count: int) -> None:
        """
        Append ``count`` text-header padding records (the shipped ``..`` spacer).

        Parameters
        ----------
        count : int
            Number of padding records to add.
        """
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
        control are overwritten. The spectrum is split across multiple records:
        the first 256 channels in the header, then 383 channels per continuation.

        Parameters
        ----------
        values : numpy.ndarray
            Spectrum reflectance values, length must match ``itchan``.
        title : str
            Spectrum title (truncated to 40 characters).
        itchan : int
            Total channel count.
        icflag : int, default=16
            Flags integer (low two bits are overwritten for record type).
        irwav : int, default=0
            Record number of the wavelength grid for this spectrum.
        irespt : int, default=0
            Record number of the resolution/FWHM grid for this spectrum.
        usernm : str, default="tetracnv"
            User name field (truncated to 8 characters).
        template : Record, optional
            Existing record whose header bytes are copied before overwriting
            controlled fields, preserving metadata like dates.

        Returns
        -------
        int
            The record number of the data header (where the spectrum starts).

        Raises
        ------
        ValueError
            If ``values.size`` does not match ``itchan``.
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
        """
        Write the accumulated library to disk.

        Parameters
        ----------
        path : str or Path
            Destination file path for the specpr library.
        """
        Path(path).write_bytes(b"".join(self.records))


def _pack_int(buf: bytearray, offset: int, value: int) -> None:
    """
    Pack a big-endian int32 into a bytearray at the given offset.

    Parameters
    ----------
    buf : bytearray
        Target buffer to modify in place.
    offset : int
        Byte offset where the int32 should be written.
    value : int
        Integer value to pack.
    """
    struct.pack_into(">i", buf, offset, value)


def _pack_text(buf: bytearray, offset: int, text: str, length: int) -> None:
    """
    Pack a fixed-length Latin-1 text field into a bytearray.

    The text is encoded, truncated if necessary, and padded with spaces to
    the specified length.

    Parameters
    ----------
    buf : bytearray
        Target buffer to modify in place.
    offset : int
        Byte offset where the text field starts.
    text : str
        Text to encode.
    length : int
        Fixed field length in bytes.
    """
    buf[offset:offset + length] = text.encode("latin1", "replace")[:length].ljust(length, b" ")


def _label_record() -> bytes:
    """
    Build record 0, the ASCII label the specpr format expects.

    Returns
    -------
    bytes
        A 1536-byte label record declaring the specpr v2 format.
    """
    body = b"SPECPR_FS=2.0\r\nRECORD_BYTES=1536\r\nLABEL_RECORDS=1\r\n"
    buf = bytearray(RECORD_BYTES)
    buf[0:len(body)] = body
    return bytes(buf)


def _text_record(title: str) -> bytes:
    """
    Build a type-2 text-header record carrying ``title`` (used for padding).

    Parameters
    ----------
    title : str
        The title text (truncated to 40 characters).

    Returns
    -------
    bytes
        A 1536-byte text-header record with the given title.
    """
    buf = bytearray(RECORD_BYTES)
    _pack_int(buf, OFF_ICFLAG, 2)
    _pack_text(buf, OFF_ITITL, title, 40)
    return bytes(buf)
