import configparser
import sys
import apis.apicorreios as correios
from datetime import datetime
from typing import Any, Dict, List, Optional

import trackingmore
from pymongo import MongoClient

import apis.apigeartrack as geartrack
from rastreio import db
from utils import status

# https://www.trackingmore.com/api-index.html - Codigos de retorno da API
config = configparser.ConfigParser()
config.read('bot.conf')

key = config['TRACKINGMORE']['key']
trackingmore.set_api_key(key)


def sort_carriers(carriers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    priorities = ["cainiao"]
    priorities.reverse()

    def get_index(carrier):
        try:
            return priorities.index(carrier["code"])
        except ValueError:
            return -1

    return sorted(carriers, key=get_index, reverse=True)


def _normalize_carrier_mapping(carrier: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(carrier, dict):
        return None

    code = carrier.get("code")
    if not isinstance(code, str) or not code.strip():
        return None

    normalized = carrier.copy()
    normalized["code"] = code.strip()
    return normalized


def _normalize_cached_carriers(
    value: Any,
) -> Optional[List[Dict[str, Any]]]:
    if isinstance(value, str):
        code = value.strip()
        return [{"code": code}] if code else None

    if isinstance(value, dict):
        carrier = _normalize_carrier_mapping(value)
        return [carrier] if carrier else None

    if isinstance(value, list) and value:
        carriers = []
        for carrier in value:
            normalized = _normalize_carrier_mapping(carrier)
            if normalized is None:
                return None
            carriers.append(normalized)
        return carriers

    return None


def get_or_create_tracking_item(carrier, code):
    print(carrier)
    try:
        tracking_data = trackingmore.get_tracking_item(carrier, code)
    except trackingmore.trackingmore.TrackingMoreAPIException as e:
        if e.err_code == 4031 or e.err_code == 4017:
            tracking_data = trackingmore.create_tracking_data(carrier, code)
            trackingmore.create_tracking_item(tracking_data)
            tracking_data = trackingmore.get_tracking_item(carrier, code)
        else:
            raise e

    return tracking_data

def get_carriers(code: str) -> List[Dict[str, Any]]:
    """Return a list of valid carrier mappings for downstream consumers.

    Commit 36150d0 documents historical string/list carrier values, but no
    production payload was available to confirm their exact shape. A non-empty
    cached string is therefore treated as a carrier code for compatibility;
    missing, empty, or malformed cached values use TrackingMore detection.
    Detection results that are not a list of mappings with non-empty string
    codes safely produce an empty list rather than leaking malformed values.
    """
    package = db.search_package(code)
    cached_value = package.get("carrier") if isinstance(package, dict) else None
    carriers = _normalize_cached_carriers(cached_value)
    if carriers is not None:
        return carriers

    try:
        detected = trackingmore.detect_carrier_from_code(code)
    except Exception as e:
        print(e)
        raise IndexError

    if isinstance(detected, list):
        carriers = []
        for carrier in detected:
            normalized = _normalize_carrier_mapping(carrier)
            if normalized is None:
                carriers = []
                break
            carriers.append(normalized)
    else:
        carriers = []

    carriers = sort_carriers(carriers)
    db.update_package(code, carrier=carriers)
    return carriers

def get(code, retries=0):
    try:
        carriers = get_carriers(code)
    except IndexError:
        return status.TYPO
    except trackingmore.trackingmore.TrackingMoreAPIException as e:
        return status.NOT_FOUND_TM

    response_status = status.NOT_FOUND_TM
    for carrier in carriers:
        try:
            if carrier['code'] == 'correios':
                codigo_novo = db.search_package(code)["code_br"]
                return correios.get(codigo_novo, 3)
        except TypeError:
            pass
        try:
            tracking_data = get_or_create_tracking_item(carrier['code'], code)
            print(tracking_data)
        except trackingmore.trackingmore.TrackingMoreAPIException as e:
            if e.err_code == 4019 or e.err_code == 4021:
                response_status = status.OFFLINE
            elif e.err_code == 4031:
                response_status = status.NOT_FOUND_TM
        else:
            if not tracking_data or 'status' not in tracking_data:
                response_status = status.OFFLINE
            elif tracking_data['status'] == 'notfound':
                response_status = status.NOT_FOUND_TM
                print(tracking_data)
            #elif len(tracking_data) >= 10:
            elif tracking_data['status'] == 'transit':
                db.update_package(code, carrier=carrier)
                return formato_obj(tracking_data, carrier, code, retries)
            elif tracking_data['status'] == 'expired':
                db.update_package(code, carrier=carrier)
                return formato_obj(tracking_data, carrier, code, retries)
            elif tracking_data['status'] == 'delivered':
                db.update_package(code, carrier=carrier)
                return formato_obj(tracking_data, carrier, code, retries)
            elif tracking_data['status'] == 'pickup':
                db.update_package(code, carrier=carrier)
                return formato_obj(tracking_data, carrier, code, retries)

    return response_status


def formato_obj(json, carrier, code, retries):
    stats = []
    stats.append(str(u'\U0001F4EE') + ' <b>' + json['tracking_number'] + '</b>')
    try:
        tabela = json['origin_info']['trackinfo']
    except KeyError:
        if retries > 0:
            return get(code, retries-1)
        else:
            return status.NOT_FOUND_TM
    for evento in reversed(tabela):
        codigo_novo = None
        print(code)
        print(evento['Date'])
        # try:
        #     data = datetime.strptime(evento['Date'], '%Y-%m-%d %H:%M:%S').strftime("%d/%m/%Y %H:%M")
        # except ValueError:
        #     data = datetime.strptime(evento['Date'], '%Y-%m-%d %H:%M').strftime("%d/%m/%Y %H:%M")
        data = evento['Date']
        situacao = evento['StatusDescription']
        observacao = evento['checkpoint_status']
        try:
            codigo_novo = geartrack.getcorreioscode(carrier['code'], code)
            if codigo_novo:
                carrier = {'code': 'correios', 'name': 'Correios'}
                db.update_package(code, carrier=carrier, code_br=codigo_novo)
                return correios.get(codigo_novo, 3)
        except:
            pass
        mensagem = ('Data: {}'
            '\nSituacao: <b>{}</b>'
            '\nObservação: {}'
        ).format(data, situacao, observacao)
        stats.append(mensagem)
    return stats

if __name__ == '__main__':
    print(get(sys.argv[1], retries=3))
