import typer
from typing import Optional, Dict, List
from aleph_client.types import AccountFromPrivateKey
from aleph_client.account import _load_account
from aleph_client.conf import settings
from pathlib import Path
import asyncio
from aleph_client import synchronous
import json
from zipfile import BadZipFile
from aleph_client.commands import help_strings

import asyncio
import json
import logging
from base64 import b32encode, b16decode
from pathlib import Path
from typing import Optional, Dict, List
from zipfile import BadZipFile

import typer
from aleph_message.models import (
    ProgramMessage,
    StoreMessage,
)

from aleph_client.types import AccountFromPrivateKey
from aleph_client.account import _load_account
from aleph_client.utils import create_archive

logger = logging.getLogger(__name__)
app = typer.Typer()


from aleph_client.asynchronous import (
    get_fallback_session,
    StorageEnum,
)

from aleph_client.commands.utils import (
    setup_logging,
    input_multiline,
    prompt_for_volumes,
    yes_no_input,
)

from aleph_message.models import (
    ProgramMessage,
    StoreMessage,
    MessagesResponse,
    ProgramContent,
)

app = typer.Typer()


@app.command()
def upload(
    path: Path = typer.Argument(..., help="Path to your source code"),
    entrypoint: str = typer.Argument(..., help="Your program entrypoint"),
    channel: str = typer.Option(settings.DEFAULT_CHANNEL, help=help_strings.CHANNEL),
    memory: int = typer.Option(
        settings.DEFAULT_VM_MEMORY, help="Maximum memory allocation on vm in MiB"
    ),
    vcpus: int = typer.Option(
        settings.DEFAULT_VM_VCPUS, help="Number of virtual cpus to allocate."
    ),
    timeout_seconds: float = typer.Option(
        settings.DEFAULT_VM_TIMEOUT,
        help="If vm is not called after [timeout_seconds] it will shutdown",
    ),
    private_key: Optional[str] = typer.Option(
        settings.PRIVATE_KEY_STRING, help=help_strings.PRIVATE_KEY
    ),
    private_key_file: Optional[Path] = typer.Option(
        settings.PRIVATE_KEY_FILE, help=help_strings.PRIVATE_KEY_FILE
    ),
    print_messages: bool = typer.Option(False),
    print_code_message: bool = typer.Option(False),
    print_program_message: bool = typer.Option(False),
    runtime: str = typer.Option(
        None,
        help="Hash of the runtime to use for your program. Defaults to aleph debian with Python3.8 and node. You can also create your own runtime and pin it",
    ),
    beta: bool = typer.Option(False),
    debug: bool = False,
    persistent: bool = False,
):
    """Register a program to run on Aleph.im virtual machines from a zip archive."""

    setup_logging(debug)

    path = path.absolute()

    try:
        path_object, encoding = create_archive(path)
    except BadZipFile:
        typer.echo("Invalid zip archive")
        raise typer.Exit(3)
    except FileNotFoundError:
        typer.echo("No such file or directory")
        raise typer.Exit(4)

    account: AccountFromPrivateKey = _load_account(private_key, private_key_file)

    runtime = (
        runtime
        or input(f"Ref of runtime ? [{settings.DEFAULT_RUNTIME_ID}] ")
        or settings.DEFAULT_RUNTIME_ID
    )

    volumes = []
    for volume in prompt_for_volumes():
        volumes.append(volume)
        typer.echo("\n")

    subscriptions: Optional[List[Dict]]
    if beta and yes_no_input("Subscribe to messages ?", default=False):
        content_raw = input_multiline()
        try:
            subscriptions = json.loads(content_raw)
        except json.decoder.JSONDecodeError:
            typer.echo("Not valid JSON")
            raise typer.Exit(code=2)
    else:
        subscriptions = None

    try:
        # Upload the source code
        with open(path_object, "rb") as fd:
            logger.debug("Reading file")
            # TODO: Read in lazy mode instead of copying everything in memory
            file_content = fd.read()
            storage_engine = (
                StorageEnum.ipfs
                if len(file_content) > 4 * 1024 * 1024
                else StorageEnum.storage
            )
            logger.debug("Uploading file")
            user_code: StoreMessage = synchronous.create_store(
                account=account,
                file_content=file_content,
                storage_engine=storage_engine,
                channel=channel,
                guess_mime_type=True,
                ref=None,
            )
            logger.debug("Upload finished")
            if print_messages or print_code_message:
                typer.echo(f"{user_code.json(indent=4)}")
            program_ref = user_code.item_hash

        # Register the program
        result: ProgramMessage = synchronous.create_program(
            account=account,
            program_ref=program_ref,
            entrypoint=entrypoint,
            runtime=runtime,
            storage_engine=StorageEnum.storage,
            channel=channel,
            memory=memory,
            vcpus=vcpus,
            timeout_seconds=timeout_seconds,
            persistent=persistent,
            encoding=encoding,
            volumes=volumes,
            subscriptions=subscriptions,
        )
        logger.debug("Upload finished")
        if print_messages or print_program_message:
            typer.echo(f"{result.json(indent=4)}")

        hash: str = result.item_hash
        hash_base32 = b32encode(b16decode(hash.upper())).strip(b"=").lower().decode()

        typer.echo(
            f"Your program has been uploaded on Aleph .\n\n"
            "Available on:\n"
            f"  {settings.VM_URL_PATH.format(hash=hash)}\n"
            f"  {settings.VM_URL_HOST.format(hash_base32=hash_base32)}\n"
            "Visualise on:\n  https://explorer.aleph.im/address/"
            f"{result.chain}/{result.sender}/message/PROGRAM/{hash}\n"
        )

    finally:
        # Prevent aiohttp unclosed connector warning
        asyncio.run(get_fallback_session().close())


@app.command()
def update(
    hash: str,
    path: Path,
    private_key: Optional[str] = settings.PRIVATE_KEY_STRING,
    private_key_file: Optional[Path] = settings.PRIVATE_KEY_FILE,
    print_message: bool = True,
    debug: bool = False,
):
    """Update the code of an existing program"""

    setup_logging(debug)

    account = _load_account(private_key, private_key_file)
    path = path.absolute()

    try:
        program_message: ProgramMessage = synchronous.get_message(
            item_hash=hash, message_type=ProgramMessage
        )
        code_ref = program_message.content.code.ref
        code_message: StoreMessage = synchronous.get_message(
            item_hash=code_ref, message_type=StoreMessage
        )

        try:
            path, encoding = create_archive(path)
        except BadZipFile:
            typer.echo("Invalid zip archive")
            raise typer.Exit(3)
        except FileNotFoundError:
            typer.echo("No such file or directory")
            raise typer.Exit(4)

        if encoding != program_message.content.code.encoding:
            logger.error(
                f"Code must be encoded with the same encoding as the previous version "
                f"('{encoding}' vs '{program_message.content.code.encoding}'"
            )
            raise typer.Exit(1)

        # Upload the source code
        with open(path, "rb") as fd:
            logger.debug("Reading file")
            # TODO: Read in lazy mode instead of copying everything in memory
            file_content = fd.read()
            logger.debug("Uploading file")
            result = synchronous.create_store(
                account=account,
                file_content=file_content,
                storage_engine=code_message.content.item_type,
                channel=code_message.channel,
                guess_mime_type=True,
                ref=code_message.item_hash,
            )
            logger.debug("Upload finished")
            if print_message:
                typer.echo(f"{result.json(indent=4)}")
    finally:
        # Prevent aiohttp unclosed connector warning
        asyncio.run(get_fallback_session().close())


@app.command()
def unpersist(
    hash: str,
    private_key: Optional[str] = settings.PRIVATE_KEY_STRING,
    private_key_file: Optional[Path] = settings.PRIVATE_KEY_FILE,
    debug: bool = False,
):
    """Stop a persistent virtual machine by making it non-persistent"""

    setup_logging(debug)

    account = _load_account(private_key, private_key_file)

    existing: MessagesResponse = synchronous.get_messages(hashes=[hash])
    message: ProgramMessage = existing.messages[0]
    content: ProgramContent = message.content.copy()

    content.on.persistent = False
    content.replaces = message.item_hash

    result = synchronous.submit(
        account=account,
        content=content.dict(exclude_none=True),
        message_type=message.type,
        channel=message.channel,
    )
    typer.echo(f"{result.json(indent=4)}")
