"""
Decoder for the tetracorder "expert system" command file (``cmd.lib.setup.*``).

Tetracorder's expert system file is a plain-text description of every material it
maps: which library spectrum identifies it, the spectral features (continuum
endpoints) that define it, and the fit/depth constraints applied.
"""
import csv
import re
from pathlib import Path
from typing import Dict


def decode(*args, **kwargs):
    """
    Convenience wrapper: decode an expert system file and return its material records.

    Constructs a :class:`TetraDecoder` (which parses the file on init) and returns
    its :attr:`~TetraDecoder.blocks` list. All arguments are forwarded verbatim to
    :class:`TetraDecoder`.

    Parameters
    ----------
    *args, **kwargs
        Passed through to :class:`TetraDecoder` (e.g. ``path``, ``groups``).

    Returns
    -------
    list[dict]
        Decoded material records for the kept groups (:attr:`TetraDecoder.blocks`).
        See :meth:`TetraDecoder.parse_block` for the per-record schema.
    """
    return TetraDecoder(*args, **kwargs).blocks


def export_csv(*args, file, **kwargs):
    """
    Convenience wrapper: decode an expert system file and write the records to CSV.

    Constructs a :class:`TetraDecoder` and calls
    :meth:`~TetraDecoder.export_csv` on it. Positional and keyword arguments (other
    than ``file``) are forwarded verbatim to :class:`TetraDecoder`.

    Parameters
    ----------
    *args, **kwargs
        Passed through to :class:`TetraDecoder` (e.g. ``path``, ``groups``).
    file : str
        Destination path for the CSV file.
    """
    TetraDecoder(*args, **kwargs).export_csv(file)



class TetraDecoder:
    """
    Decode a Tetracorder expert system file into structured per-material records.

    The expert system file (``cmd.lib.setup.<version>``) is organized into "group
    blocks", one per mapped material. Each block names the library spectrum used to
    identify the material, the spectral features (continuum endpoints plus fit/depth
    thresholds) that define it, the constituent constraints applied, and the output
    file the mapper writes. :meth:`decode` walks the file and turns every block whose
    ``group`` is in ``only`` into a plain ``dict`` (see :meth:`parse_block` for the
    schema); the results are stored on :attr:`blocks`.

    Several fields in the file are written as ``[NAME]`` references to variables
    defined in the companion ``cmd.lib.setup.variables`` file, and output paths are
    keyed by group directory in ``cmds.start.*``. When those sidecar files are found
    next to the expert system file they are parsed up front (:attr:`vars`,
    :attr:`groups`) and substituted in during decoding.

    Attributes
    ----------
    file : Path
        Resolved path to the expert system file being decoded.
    vars : dict[str, str] or None
        Variable name -> value map from ``cmd.lib.setup.variables``, or ``None`` if
        that file was not found.
    groups : dict[int, str]
        Group number -> output directory prefix, from ``cmds.start.*``.
    only : tuple[int, ...]
        Group numbers to keep; blocks in other groups go to :attr:`ignored`.
    blocks : list[dict]
        Decoded material records for the kept groups (populated by :meth:`decode`).
    ignored : list[dict]
        Decoded records whose group was not in :attr:`only`.
    """

    vars = None
    nots = None

    def __init__(self, path, groups=(1, 2)):
        """
        Parameters
        ----------
        path : str
            Path to the Tetracorder expert system file (``cmd.lib.setup.<version>``).
            May also be a directory, in which case the first file matching
            ``cmd.lib.setup.t*`` inside it is used.
        groups : tuple[int, ...], optional
            Group numbers to decode. Blocks belonging to any other group are parsed
            but set aside in :attr:`ignored` rather than :attr:`blocks`. Defaults to
            ``(1, 2)`` (the group 1 / group 2 minerals of the EMIT L2B product).
        """
        self.file = Path(path)
        if self.file.is_dir():
            self.file = next(self.file.glob("cmd.lib.setup.t*"))

        # Retrieve external references first, if available
        self.root = self.file.parent
        if (f := self.root / "cmd.lib.setup.variables").exists():
            self.vars = self.parse_variables(f)

        if files := list(self.root.glob(f"cmds.start.*")):
            self.groups = self.parse_group_paths(files[0])

        # Unused
        # if (f := self.root / "cmd.lib.setup.nots-ratios").exists():
        #     self.nots = self.parse_not_ratios(f)

        self.only = groups
        if not groups:
            self.only = list(self.groups)

        self.decode()

    def decode(self):
        """
        Read the expert system file and parse every group block.

        The file is read line by line into :attr:`lines`, split into blocks by
        :meth:`extract_blocks`, and each block is parsed by :meth:`parse_block`.
        Parsed blocks are sorted into :attr:`blocks` (group in :attr:`only`) or
        :attr:`ignored` (any other group).

        Returns
        -------
        list[dict]
            The kept blocks, i.e. :attr:`blocks`.
        """
        self.lines = []
        with self.file.open("r") as file:
            for line in file:
                line = line.strip()
                self.lines.append(line)


        self.blocks = []
        self.ignored = []
        for block in self.extract_blocks(self.lines):
            block = self.parse_block(block)
            if block["group"] in self.only:
                self.blocks.append(block)
            else:
                self.ignored.append(block)

        return self.blocks

    def parse_block(self, block):
        """
        Parse a single group block into a material record.

        Each block is a list of stripped, comment-free lines (as produced by
        :meth:`extract_blocks`). Recognized directives are pulled out into a ``dict``;
        any ``[NAME]`` references are substituted with their values from :attr:`vars`.

        Parameters
        ----------
        block : list[str]
            Lines of one group block from the expert system file.

        Returns
        -------
        dict
            A material record with any of the following keys, depending on which
            directives the block contains:

            ``group`` : int
                Group number the material belongs to.
            ``use`` : str
                Whether the entry is active (``"yes"``/``"no"``).
            ``name`` : str
                Short material name (first token after ``TITLE=``).
            ``title`` : str
                Full material title.
            ``library`` : str
                Spectral library ID the reference record lives in (e.g. ``splib06``).
            ``record`` : int
                Record number of the reference spectrum within ``library``.
            ``filename`` : str
                Output base file name written by the mapper.
            ``path`` : str
                ``filename`` prefixed with its group output directory, when the group
                directory is known from :attr:`groups`.
            ``data_type_scaling`` : float
                Scaling applied to the 8-bit (``0-255``) output DN.
            ``features`` : list[dict]
                Spectral features. Each feature has ``feature_type`` (``DLw``/``MLw``/
                ``OLw``), a 4-element ``continuum`` (left/right window endpoints), and
                one list per fit/depth constraint keyword found on the feature line
                (e.g. ``ct``, ``rcbblc<``).
            ``constituent_constraints`` : dict[str, list[float]]
                Per-block constraint keyword -> values (e.g. ``FD-FIT>``, ``FITALL>``).
        """
        data = {}
        for i, line in enumerate(block):
            if line.startswith("group"):
                data["group"] = int(line.split()[1])

            if line.startswith("use="):
                data["use"] = line.split(" ")[1]

            elif "=- TITLE=" in line:
                split = line.split("TITLE=")[1].split()
                data["name"] = split[0]
                data["title"] = " ".join(split)

            elif line.startswith("define library records"):
                split = block[i+1].split()
                data["library"] = split[2].strip("[]")
                data["record"] = int(split[3])

            elif line.startswith("define output"):
                data["filename"] = block[i+2]
                data["path"] = None
                if path := self.groups.get(data["group"]):
                    data["path"] = path + data["filename"]

            elif line.startswith("8 DN 255"):
                val = line.split(" ")[-1]
                val = self.vars.get(val.strip("[]"), val) # Substitute variables, if necessary
                data["data_type_scaling"] = float(val)

            elif line.startswith("define features"):
                data["features"] = []
                while (line := block[i]) != "endfeatures":
                    i += 1
                    # Substitute variables, if necessary
                    split = [self.vars.get(v.strip("[]"), v) for v in line.split()]
                    if line.startswith("f") and split[1] in ("DLw", "MLw", "OLw"):
                        feat = {
                            "feature_type": split[1],
                            "continuum": list(map(float, split[2:6])),
                        }

                        line = " ".join(split[6:])
                        matches = re.findall(r"([^\s\d]+)>?\s+(.+?)(?=\s+[^\s\d]+>?[\s]|$)", line)
                        for key, vals in matches:
                            feat[key] = list(map(float, vals.split()))

                        data["features"].append(feat)


            elif line.startswith("constraint:"):
                consts = data.setdefault("constituent_constraints", {})
                # Match successive KEY>VALUE pairs, where VALUE extends until the next KEY> or end of line
                matches = re.findall(r"([^\s>]+)>\s*(.*?)(?=\s+[^\s>]+>|$)", line)
                for key, val in matches:
                    val = self.vars.get(val.strip("[]"), val)
                    consts[key] = list(map(float, val.split()))

        return data

    def export_csv(self, file, groups=None):
        """
        Write the decoded records to a CSV file with ``record``, ``title``, ``path``.

        Emits one row per material across both :attr:`blocks` and :attr:`ignored`
        whose group is in ``groups``, so records outside :attr:`only` can be exported
        by widening ``groups``.

        Parameters
        ----------
        file : str
            Destination path for the CSV file.
        groups : Iterable[int], optional
            Group numbers to include. Defaults to :attr:`only` (the groups that were
            decoded into :attr:`blocks`).
        """
        if groups is None:
            groups = self.only

        data = [("record", "title", "path")]
        for block in self.blocks + self.ignored:
            if block["group"] in groups:
                data.append((
                    block["record"],
                    block["title"],
                    block["path"]
                ))

        with open(file, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerows(data)

    @staticmethod
    def parse_variables(file: str) -> Dict[str, str]:
        """
        Parse ==[NAME] value... definitions from a command file.

        This function extracts variable definitions from tetracorder command files,
        which use a special ==[NAME] syntax to define parameters. The values are
        returned as strings to preserve formatting and precision.

        Parameters
        ----------
        file : str
            Path to the command file to parse.

        Returns
        -------
        Dict[str, str]
            Mapping of variable names to their string values (whitespace-stripped).
            If a variable is defined multiple times, the last definition wins.

        Examples
        --------
        Given a file with content:
            ==[THRESHOLD] 0.5
            ==[VALUES] 1.0 2.0 3.0
            ==[THRESHOLD] 0.8

        >>> parse_variables("cmd.file")
        {'THRESHOLD': '0.8', 'VALUES': '1.0 2.0 3.0'}
        """
        text = Path(file).read_text()

        pattern = re.compile(
            r"^==\[(?P<name>[^\]]+)\]\s*(?P<values>[-+0-9.eE\s]+)",
            re.MULTILINE,
        )

        return {
            match["name"]: match["values"].strip()
            for match in pattern.finditer(text)
        }

    @staticmethod
    def parse_not_ratios(file):
        """
        Parse the ``cmd.lib.setup.nots-ratios`` "not feature" definitions.

        These entries use a ``==[NAME] [SOURCE] value...`` syntax, grouping variable
        definitions under a source library key (e.g. ``rec1``, ``Bckm``). Values are
        kept as whitespace-stripped strings.

        Parameters
        ----------
        file : str
            Path to the ``cmd.lib.setup.nots-ratios`` file.

        Returns
        -------
        dict[str, dict[str, str]]
            Nested mapping ``{source: {name: value}}``. If a name is repeated within
            a source, the last definition wins.
        """
        text = Path(file).read_text()

        pattern = re.compile(
            r"^==\[(?P<name>[^\]]+)\]\s*\[(?P<source>[\w]+)\]\s*(?P<values>[-+0-9.eE\s]+)",
            re.MULTILINE,
        )

        data = {}
        for m in pattern.finditer(text):
            source = data.setdefault(m["source"], {})
            source[m["name"]] = m["values"].strip()

        return data

    @staticmethod
    def parse_group_paths(file: str) -> Dict[int, str]:
        """
        Parse the per-group output directories from a ``cmds.start.*`` file.

        Group directories are defined with a ``==[DIRg<N>]<path>`` syntax, e.g.
        ``==[DIRg1]group.1um/``. These prefixes are prepended to each block's output
        filename in :meth:`parse_block`.

        Parameters
        ----------
        file : str
            Path to the ``cmds.start.*`` file.

        Returns
        -------
        Dict[int, str]
            Mapping of group number to its output directory prefix.
        """
        text = Path(file).read_text()

        matches = re.findall(r"^==\[DIRg(\d+)\](.+)$", text, flags=re.MULTILINE)

        return {int(group): path for group, path in matches}

    @staticmethod
    def extract_blocks(lines):
        """
        Split the expert system lines into group blocks.

        A block opens on a line matching ``group <N>`` and closes on the line
        starting with ``endaction``. Within a block, trailing ``\\#`` comments are
        stripped (except on ``TITLE`` lines, where ``#`` may be significant) and blank
        lines are dropped, so each yielded block is a list of meaningful lines only.

        Parameters
        ----------
        lines : Iterable[str]
            Stripped lines of the expert system file.

        Yields
        ------
        list[str]
            One group block, from its ``group <N>`` line through ``endaction``.
        """
        block = None
        for line in lines:
            if re.match(r"^group\s+\d", line):
                block = []

            if block is not None:
                # Strip comments from the line
                if "TITLE" not in line:
                    line = line.split("\\#")[0].strip()

                if line:
                    block.append(line)
                    if line.startswith("endaction"):
                        yield block
                        block = None
