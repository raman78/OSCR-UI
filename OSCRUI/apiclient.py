from pathlib import Path
from typing import Any, Callable

from requests import Session


def _empty_error_handler(error: BaseException):
    return


class Ladder():
    """Represents a ladder of the league"""

    __slots__ = ('id', 'name', 'difficulty', 'is_solo')

    def __init__(self, id: int, name: str, difficulty: str | None, is_solo: bool):
        self.id: int = id
        self.name: str = name
        self.difficulty: str | None = difficulty
        self.is_solo: bool = is_solo

    def __repr__(self) -> str:
        return (
            f'<Ladder id={self.id} name="{self.name}" difficulty={self.difficulty} '
            f'is_solo={self.is_solo}>')


class LadderEntry():
    """Represents an entry of a ladder"""

    __slots__ = ('rank', 'combat_date', 'data', 'combatlog_id')

    def __init__(self, rank: int, combat_date: str, data: dict[str], combatlog_id: int):
        self.rank: int = rank
        self.combat_date: str = combat_date
        self.data: dict[str] = data
        self.combatlog_id: int = combatlog_id

    def __repr__(self) -> str:
        return (
            f'<Ladder rank={self.rank} combat_date={self.combat_date} '
            f'combatlog_id={self.combatlog_id}>')


class UploadEntry():
    """Represents a newly created or rejected ladder entry"""

    __slots__ = ('name', 'updated', 'detail', 'value')

    def __init__(self, name: str, updated: bool, detail: str, value: float):
        self.name: str = name
        self.updated: bool = updated
        self.detail: str = detail
        self.value: float = value

    def __repr__(self) -> str:
        return f'<UploadEntry name="{self.name}" updated={self.updated}>'


class UploadResult():
    """Contains information about upload attempt"""

    __slots__ = ('detail', 'combatlog_id', 'entries')

    def __init__(self, detail: str, combatlog_id: int):
        self.detail: str = detail
        self.combatlog_id: int = combatlog_id
        self.entries: list[UploadEntry] = list()

    def add_entry(self, entry: UploadEntry):
        """
        Adds an entry to the list of entries.

        Parameters:
        - :param entry: entry to add
        """
        self.entries.append(entry)

    def __repr__(self) -> str:
        return f'<UploadResult detail="{self.detail}" combatlog_id={self.combatlog_id}>'


class OSCRApiClient():
    """Client for interacting with the OSCR League Server"""

    def __init__(
            self, backend_url: str, temp_log_dir: Path,
            error_handler: Callable[[BaseException], Any] = _empty_error_handler):
        self.backend_url: str = backend_url
        self.temp_log_dir: str = str(temp_log_dir)
        self.error_handler: Callable[[BaseException], Any] = error_handler
        self._session: Session = Session()
        self._session.headers['Accept'] = 'application/json'

    def variants(self, ordering: str = '') -> list[str] | None:
        """
        Fetches list of variants (=Seasons)

        Parameters:
        - :param ordering: ordering parameter as accepted by Django REST api
        """
        try:
            response = self._session.get(
                f'{self.backend_url}/variant', params={'ordering': ordering}, timeout=(3, 20))
            response.raise_for_status()
            response_data = response.json()
        except BaseException as e:
            self.error_handler(e)
            return
        return [variant['name'] for variant in response_data['results']]

    def ladders(self, season: str) -> list[Ladder] | None:
        """
        Fetches list of ladders (=maps) for given season

        Parameters:
        - :param season: name of the season to fetch ladders for
        """
        try:
            response = self._session.get(
                f'{self.backend_url}/ladder', params={'variant': season}, timeout=(3, 20))
            response.raise_for_status()
            response_data = response.json()
        except BaseException as e:
            self.error_handler(e)
            return
        ladders = list()
        for ladder in response_data['results']:
            ladders.append(Ladder(
                ladder['id'], ladder['name'], ladder['difficulty'], ladder['is_solo']))
        return ladders

    def ladder_entries(
            self, ladder_id: int, ordering: str = '', page_size: int = 50, page: int = 1,
            player_filter: str = '') -> list[LadderEntry] | None:
        """
        Fetches list of ladder entries for given ladder.

        Parameters:
        - :param ladder_id: identifies the ladder, obtained from `/ladder` api endpoint
        - :param ordering: ordering parameter as accepted by Django REST api
        - :param page_size: maximum number of entries per page
        - :param page: page number
        - :param player_filter: search string to filter entries by; returns entries that have a \
            player name containing the search string
        """
        try:
            query_parameters = {
                'ladder': ladder_id,
                'ordering': ordering,
                'page_size': page_size,
                'page': page,
                'player__icontains': player_filter
            }
            response = self._session.get(
                f'{self.backend_url}/ladder-entries', params=query_parameters, timeout=(3, 20))
            response.raise_for_status()
            response_data = response.json()
        except BaseException as e:
            self.error_handler(e)
            return
        entries = list()
        for entry in response_data['results']:
            entries.append(LadderEntry(
                entry['ladder_rank'], entry['date'], entry['data'], entry['combatlog']))
        return entries

    def upload_combatlog(self, combatlog_data: bytes) -> UploadResult | None:
        """
        Uploads combatlog to server and receives server response for uploaded combatlog.

        Parameters:
        - :param combatlog_data: gzip compressed combatlog
        """
        try:
            response = self._session.post(
                f'{self.backend_url}/combatlog/uploadv2/', files={'file': combatlog_data},
                timeout=(3, 60))
            response.raise_for_status()
            response_data = response.json()
        except BaseException as e:
            self.error_handler(e)
            return
        result = UploadResult(response_data['detail'], response_data['combatlog'])
        for entry in response_data['results']:
            result.add_entry(
                UploadEntry(entry['name'], entry['updated'], entry['detail'], entry['value']))
        return result

    def download_combatlog(self, combatlog_id: int) -> bytes | None:
        """
        Downloads specified combatlog and returns the compressed bytes of the file.

        Parameters:
        - :param combatlog_id: id of the combatlog (obtained from /ladder-entries API endpoint)
        """
        try:
            response = self._session.get(
                f'{self.backend_url}/combatlog/{combatlog_id}/download/', timeout=(3, 60))
            response.raise_for_status()
            return response.content
        except BaseException as e:
            self.error_handler(e)
