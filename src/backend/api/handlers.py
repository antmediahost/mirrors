# coding=utf-8
import os
from collections import defaultdict
from time import sleep
from typing import (
    AnyStr,
    List,
    Dict,
)

from api.exceptions import BadRequestFormatExceptioin
from api.mirrors_update import (
    get_config,
    REQUIRED_MIRROR_PROTOCOLS,
    get_mirrors_info,
    ARCHS,
    update_mirror_in_db,
)
from api.utils import get_geo_data_by_ip
from db.models import (
    Url,
    Mirror,
)
from db.utils import session_scope
from sqlalchemy.sql.expression import false

from common.sentry import (
    get_logger,
)


MAX_LENGTH_OF_MIRRORS_LIST = 10

logger = get_logger(__name__)


def _get_nearest_mirrors(
        ip_address: AnyStr,
        empty_for_unknown_ip: bool = False,
):
    """
    The function returns N nearest mirrors towards a request's IP
    Firstly, it searches first N mirrors inside a request's country
    Secondly, it searches first N nearest mirrors by distance
        inside a request's continent
    Thirdly, it searches first N nearest mirrors by distance in the world
    Further the functions concatenate lists and return first
        N elements of a summary list
    :param empty_for_unknown_ip: if True and we can't get geo data of an IP
        the function returns empty list
    """
    if os.environ.get('DEPLOY_ENVIRONMENT').lower() in (
        'dev',
        'development',
    ):
        ip_address = os.environ.get(
            'TEST_IP_ADDRESS',
        ) or '195.123.213.149'
    match = get_geo_data_by_ip(ip_address)
    with session_scope() as session:
        all_mirrors_query = session.query(Mirror).filter(
            Mirror.is_expired == false(),
        )
        # We return all of mirrors if we can't
        # determine geo data of a request's IP
        if match is None and not empty_for_unknown_ip:
            all_mirrors = [
                mirror.to_dict() for mirror in all_mirrors_query.all()
            ]
            return all_mirrors
        elif match is None:
            return []
        continent, country, latitude, longitude = match
        # get n-mirrors in a request's country
        mirrors_by_country_query = session.query(Mirror).filter(
            Mirror.continent == continent,
            Mirror.country == country,
            Mirror.is_expired == false(),
        ).limit(
            MAX_LENGTH_OF_MIRRORS_LIST,
        )
        # get n-mirrors mirrors inside a request's continent
        # but outside a request's contry
        mirrors_by_continent_query = session.query(Mirror).filter(
            Mirror.continent == continent,
            Mirror.country != country,
            Mirror.is_expired == false(),
            ).order_by(
            Mirror.conditional_distance(
                lon=longitude,
                lat=latitude,
            )
        ).limit(
            MAX_LENGTH_OF_MIRRORS_LIST,
        )
        # get n-mirrors mirrors from all of mirrors outside
        # a request's country and continent
        all_rest_mirrors_query = session.query(Mirror).filter(
            Mirror.is_expired == false(),
            Mirror.continent != continent,
            Mirror.country != country,
            ).order_by(
            Mirror.conditional_distance(
                lon=longitude,
                lat=latitude,
            )
        ).limit(
            MAX_LENGTH_OF_MIRRORS_LIST,
        )

        mirrors_by_country = mirrors_by_country_query.all()
        mirrors_by_continent = mirrors_by_continent_query.all()
        all_rest_mirrors = all_rest_mirrors_query.all()
        suitable_mirrors = mirrors_by_country + \
            mirrors_by_continent + \
            all_rest_mirrors

        # TODO: SQLAlchemy adds brackets around queries. And it looks like
        # TODO: incorrect query for SQLite
        # suitable_mirrors_query = mirrors_by_country_query.union_all(
        #     mirrors_by_continent_query,
        # ).union_all(
        #     all_rest_mirrors_query,
        # ).limit(MAX_LENGTH_OF_MIRRORS_LIST)
        # suitable_mirrors = suitable_mirrors_query.all()

        # return n-nearst mirrors
        suitable_mirrors = [mirror.to_dict() for mirror
                            in suitable_mirrors[:MAX_LENGTH_OF_MIRRORS_LIST]]
        return suitable_mirrors


def update_mirrors_handler():
    config = get_config()
    versions = config['versions']
    repos = config['repos']
    mirrors_dir = os.path.join(
        os.path.dirname(
            os.path.abspath(__file__),
        ),
        '../../../mirrors/updates',
        config['mirrors_dir'],
    )
    all_mirrors = get_mirrors_info(
        mirrors_dir=mirrors_dir,
    )
    with session_scope() as session:
        session.query(Mirror).filter(
            Mirror.name in [mirror_info['name'] for mirror_info in all_mirrors]
        ).delete()
        session.flush()
    for mirror_info in all_mirrors:
        update_mirror_in_db(
            mirror_info,
            versions,
            repos,
            config['allowed_outdate'],
        )
        sleep(1)
    return 'Done'


def get_all_mirrors():
    mirrors_list = []
    with session_scope() as session:
        mirrors = session.query(
            Mirror
        ).order_by(
            Mirror.continent,
            Mirror.country,
        ).all()
        for mirror in mirrors:
            mirror_data = mirror.to_dict()
            mirrors_list.append(mirror_data)
    return mirrors_list


def get_mirrors_list(
        ip_address: AnyStr,
        version: AnyStr,
        repository: AnyStr,
) -> AnyStr:
    mirrors_list = []
    config = get_config()
    versions = [str(version) for version in config['versions']]
    if version not in versions:
        raise BadRequestFormatExceptioin(
            'Unknown version "%s". Allowed list of versions "%s"',
            version,
            ', '.join(versions),
        )
    repos = {repo['name']: repo['path'] for repo in config['repos']}
    if repository not in repos:
        raise BadRequestFormatExceptioin(
            'Unknown repository "%s". Allowed list of repositories "%s"',
            repository,
            ', '.join(repos.keys()),
        )
    repo_path = repos[repository]
    nearest_mirrors = _get_nearest_mirrors(ip_address=ip_address)
    for mirror in nearest_mirrors:
        mirror_url = mirror['urls'].get(REQUIRED_MIRROR_PROTOCOLS[0]) or \
                     mirror['urls'].get(REQUIRED_MIRROR_PROTOCOLS[1])
        full_mirror_path = os.path.join(
            mirror_url,
            version,
            repo_path
        )
        mirrors_list.append(full_mirror_path)

    return '\n'.join(mirrors_list)


def _set_isos_link_for_mirror(
        mirror_info: Dict,
        version: AnyStr,
        arch: AnyStr,
):
    addresses = mirror_info['urls']
    mirror_url = next(iter([
        address for protocol_type, address in
        addresses.items()
        if protocol_type in REQUIRED_MIRROR_PROTOCOLS
    ]))
    mirror_info['isos_link'] = os.path.join(
        mirror_url,
        str(version),
        'isos',
        arch,
    )


def get_isos_list_by_countries(
        arch: AnyStr,
        version: AnyStr,
        ip_address: AnyStr,
):
    mirrors_by_countries = defaultdict(list)
    for mirror_info in get_all_mirrors():
        mirrors_by_countries[mirror_info['country']].append(mirror_info)
    for country, country_mirrors in \
            mirrors_by_countries.items():
        for mirror_info in country_mirrors:
            _set_isos_link_for_mirror(
                mirror_info=mirror_info,
                version=version,
                arch=arch
            )
    nearest_mirrors = _get_nearest_mirrors(
        ip_address=ip_address,
        empty_for_unknown_ip=True,
    )
    for nearest_mirror in nearest_mirrors:
        _set_isos_link_for_mirror(
            mirror_info=nearest_mirror,
            version=version,
            arch=arch
        )
    return mirrors_by_countries, nearest_mirrors


def get_main_isos_table():
    result = defaultdict(list)
    config = get_config()
    versions = config['versions']
    duplicated_versions = config['duplicated_versions']
    for arch in ARCHS:
        result[arch] = [version for version in versions
                        if version not in duplicated_versions]

    return result


def get_url_types() -> List[AnyStr]:
    with session_scope() as session:
        return [value[0] for value in session.query(
            Url.type
        ).distinct()]
