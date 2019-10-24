""" This is the simplest aleph network client available.
"""
from binascii import hexlify
import time
import aiohttp
import asyncio
import json
import hashlib

DEFAULT_SERVER = "https://api1.aleph.im"

FALLBACK_SESSION = None

async def get_fallback_session():
    global FALLBACK_SESSION
    if FALLBACK_SESSION is None:
        FALLBACK_SESSION = aiohttp.ClientSession()
    return FALLBACK_SESSION

def wrap_async(func):
    def func_caller(*args, **kwargs):
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(func(*args, **kwargs))
    return func_caller


async def ipfs_push(content, session=None, api_server=DEFAULT_SERVER):
    if session is None:
        session = await get_fallback_session()
        
    async with session.post("%s/api/v0/ipfs/add_json" % api_server,
                            json=content) as resp:
        return (await resp.json()).get('hash')

sync_ipfs_push = wrap_async(ipfs_push)

async def broadcast(message, session=None, api_server=DEFAULT_SERVER):
    if session is None:
        session = await get_fallback_session()
        
    async with session.post("%s/api/v0/ipfs/pubsub/pub" % api_server,
                            json={'topic': 'ALEPH-TEST',
                               'data': json.dumps(message)}) as resp:
        return (await resp.json()).get('value')

sync_broadcast = wrap_async(broadcast)


async def create_post(account, post_content, post_type, address=None,
                      channel='TEST', session=None, api_server=DEFAULT_SERVER):        
    if address is None:
        address = account.get_address()

    post = {
        'type': post_type,
        'address': address,
        'content': post_content,
        'time': time.time()
    }
    return await submit(account, post, 'POST', channel=channel,
                        api_server=api_server, session=session)

sync_create_post = wrap_async(create_post)


async def create_aggregate(account, key, content, address=None,
                           channel='TEST', session=None, api_server=DEFAULT_SERVER):
    if address is None:
        address = account.get_address()

    post = {
        'key': key,
        'address': address,
        'content': content,
        'time': time.time()
    }
    return await submit(account, post, 'AGGREGATE', channel=channel,
                        api_server=api_server, session=session)

sync_create_aggregate = wrap_async(create_aggregate)


async def submit(account, content, message_type, channel='IOT_TEST',
                 api_server=DEFAULT_SERVER, session=None, inline=True):    
    message = {
      #'item_hash': ipfs_hash,
      'chain': account.CHAIN,
      'channel': channel,
      'sender': account.get_address(),
      'type': message_type,
      'time': time.time()
    }
    
    if inline:
        message['item_content'] = json.dumps(content, separators=(',',':'))
        h = hashlib.sha256()
        h.update(message['item_content'].encode('utf-8'))
        message['item_hash'] = h.hexdigest()
    else:
        message['item_hash'] = ipfs_push(content, api_server=api_server)
        
    message = account.sign_message(message)
    await broadcast(message, session=session, api_server=api_server)
    return message

sync_submit = wrap_async(submit)


async def fetch_aggregate(address, key, session=None, api_server=DEFAULT_SERVER):
    if session is None:
        session = await get_fallback_session()
        
    async with session.get("%s/api/v0/aggregates/%s.json?keys=%s" % (
        api_server, address, key
    )) as resp:
        return (await resp.json()).get('data', dict()).get(key)

sync_fetch_aggregate = wrap_async(fetch_aggregate)
