# SPDX-FileCopyrightText: Copyright (c) 2024-2025 Yegor Bugayenko
# SPDX-License-Identifier: MIT

from __future__ import annotations

import datetime
import sys
from copy import deepcopy
from pathlib import Path
from typing import Literal, TypeAlias, TypedDict, Protocol, final, override

import attrs
import httpx
import yaml


class InvalidUrlError(Exception):
    """Exception threw on fail ping url."""


class ExpiredCfpError(Exception):
    """Exception threw on call for papers date expired."""


DateAsStrT: TypeAlias = str
ClosedStrLiteral = "closed"
UrlStrLiteral = "url"
RankStrLiteral = "rank"
CfpStrLiteral = "cfp"
RawDateT: TypeAlias = DateAsStrT | Literal[ClosedStrLiteral]


class ConfInfoDict(TypedDict):

    name: str
    year: str
    url: str
    publisher: str
    rank: str
    core: str
    scope: str
    short: str
    full: str
    format: str
    cfp: str
    country: str


class String(Protocol):

    def to_str(self) -> str: ...


class Date(Protocol):

    def to_date(self) -> datetime.date: ...


@final
@attrs.define(frozen=True)
class CfName(String):
    _name: str
    _inf: ConfInfoDict

    @override
    def to_str(self) -> str:
        """Build name.

        >>> CfName('ABC', {'year': '2099', 'url': 'https://google.com', 'later': False}).to_str()
        "[ABC'99](<https://google.com>)"
        >>> CfName('ABC', {'year': 2099, 'url': 'https://google.com', 'later': False}).to_str()
        "[ABC'99](<https://google.com>)"
        """
        year = str(self._inf["year"])[-2:]
        return "[{0}'{1}](<{2}>)".format(
            self._name,
            year,
            self._inf[UrlStrLiteral] if self._inf["later"] else ValidUrl(self._inf[UrlStrLiteral]).to_str(),
        )


@final
@attrs.define(frozen=True)
class ActualDate(Date):

    _date: datetime.date

    def to_date(self) -> datetime.date:
        today = datetime.datetime.now(tz=datetime.UTC).date()
        if self._date > today:
            return self._date
        msg = f"{self._date} expired for today {today}"
        raise ExpiredCfpError(msg)


@final
@attrs.define(frozen=True)
class CfDate(String):
    _date: RawDateT | None

    @override
    def to_str(self) -> str:
        """Render date.

        >>> CfDate("2090-01-01").to_str()
        '90-Jan'
        >>> CfDate("closed").to_str()
        'closed'
        >>> CfDate(None).to_str()
        ''
        """
        if not self._date:
            return ""
        if self._date == ClosedStrLiteral:
            return ClosedStrLiteral
        parsed = datetime.datetime.strptime(
            self._date, "%Y-%m-%d",
        ).replace(tzinfo=datetime.UTC).date()
        return parsed.strftime("%y-%b")


@final
@attrs.define(frozen=True)
class TableRow(String):
    _name: str
    _inf: list[dict]
    _template: str

    @override
    def to_str(self) -> str:
        return self._template.format(
            name=CfName(self._name, self._inf).to_str(),
            publisher=self._inf["publisher"] or "",
            rank="[{0}](<{1}>)".format(
                self._inf[RankStrLiteral],
                self._inf[UrlStrLiteral] if self._inf["later"] else ValidUrl(self._inf["core"]).to_str(),
            ),
            scope=self._inf["scope"],
            short=self._inf["short"] or "",
            full=self._inf["full"] or "",
            format=self._inf["format"] or "",
            cfp=CfDate(self._inf[CfpStrLiteral]).to_str(),
            country=self._inf["country"],
        )


class TblRows(Protocol):

    def rows(self) -> list[str]: ...


@final
@attrs.define(frozen=True)
class TableRows(TblRows):
    _yml: dict[str, ConfInfoDict]
    _template: str
    
    def rows(self) -> list[str]:
        return [
            TableRow(name, inf, self._template).to_str()
            for name, inf in sorted(
                self._yml.items(),
                key=_sort_key,
            )
        ]


def _sort_key(elem: str) -> int:
    srtd = {
        char: idx
        for idx, char in enumerate(["A*", "A", "B", "C", "D", "E", "F"])
    }
    return srtd[elem[1][RankStrLiteral]]


@final
@attrs.define(frozen=True)
class ValidUrl(String):
    _url: str

    def to_str(self):
        response = httpx.get(self._url)
        success = httpx.codes.is_success(response.status_code)
        allow = success or httpx.codes.is_redirect(response.status_code)
        if not allow:
            msg = f"Url = '{self._url}' return status = {response.status_code}"
            raise InvalidUrlError(msg)
        return self._url


def mark_expired_dates(path: str) -> None:
    yml = Path(path).read_text()
    origin = yaml.safe_load(yml)
    updated = deepcopy(origin)
    for name, inf in yaml.safe_load(yml).items():
        if not inf[CfpStrLiteral] or inf[CfpStrLiteral] == ClosedStrLiteral:
            continue
        try:
            ActualDate(
                datetime.datetime.strptime(
                    inf[CfpStrLiteral],
                    "%Y-%m-%d",
                ).replace(tzinfo=datetime.UTC).date(),
            ).to_date()
        except ExpiredCfpError:
            updated[name][CfpStrLiteral] = ClosedStrLiteral
    write_yaml_file(path, updated)


def write_yaml_file(path: str, yml: ConfInfoDict) -> None:
    records = []
    for name, record in yml.items():
        dumped = yaml.safe_dump({name: record})
        lines = sorted(
            dumped.splitlines()[1::],
            key=_sort_lines_key,
        )
        records.append(
            "{0}\n{1}\n".format(
                dumped.splitlines()[0],
                "\n".join(lines),
            ),
        )
    Path(path).write_text(
        "{0}---\n{1}".format(
            Path(path).read_text().split("---")[0],
            "\n".join(records),
        ),
    )


def _sort_lines_key(line: str) -> int:
    weights = {
        name: idx
        for idx, name in enumerate([
            "year",
            UrlStrLiteral,
            "publisher",
            RankStrLiteral,
            "core",
            "scope",
            "short",
            "full",
            "format",
            CfpStrLiteral,
            "country",
            "later",
        ])
    }
    return weights[line.strip().split(":")[0]]


def generate(yml: str, md: str) -> None:
    mark_expired_dates(yml)
    headers = [
        "name",
        "publisher",
        RankStrLiteral,
        "scope",
        "short",
        "full",
        "format",
        CfpStrLiteral,
        "country",
    ]
    template = "".join([
        "| {name} ",
        "| {publisher} ",
        "| {rank} ",
        "| {scope} ",
        "| {short} ",
        "| {full} ",
        "| {format} ",
        "| {cfp} ",
        "| {country} |",
    ])
    rows = ["| {0} |".format(" | ".join(headers))]
    rows.append(
        "| {0} |".format(
            " | ".join(["---" for _ in range(len(headers))]),
        ),
    )
    rows.extend(
        TableRows(
            yaml.safe_load(Path(yml).read_text()),
            template,
        ).rows(),
    )
    sep = "<!-- events -->"
    split = Path(md).read_text().split(sep)
    split[1] = "\n{0}\n\n".format("\n".join(rows))
    Path(md).write_text(sep.join(split))


if __name__ == "__main__":
    generate(sys.argv[1], sys.argv[2])  # pragma: no cover
