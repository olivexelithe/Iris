import asyncio
import os
import random
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks


TASK_PATTERN = re.compile(
    r"^\s*(?:<@!?(?P<mention>\d+)>\s*|<@&(?P<rolemention>\d+)>\s*|@(?P<atname>olive|elithe|eli|admin)\s*|(?P<name>olive|elithe|eli|admin)\s*)"
    r",?\s+can\s+you\s+(?P<task>.+?)\s*[?.!]*\s*$",
    re.IGNORECASE,
)
TASK_LIST_PATTERN = re.compile(
    r"^\s*iris,?\s+(?:what\s+tasks\s+have\s+i\s+got|what\s+tasks\s+do\s+i\s+have|what\s+are\s+my\s+tasks|show\s+me\s+my\s+tasks|show\s+my\s+tasks|show\s+open\s+tasks|list\s+my\s+tasks|my\s+tasks|tasks|show\s+tasks)\s*[?.!]*\s*$",
    re.IGNORECASE,
)
TASK_LIST_EVERYTHING_PATTERN = re.compile(
    r"^\s*iris,?\s+show\s+everything\s*[?.!]*\s*$",
    re.IGNORECASE,
)
FUZZY_TASK_LIST_PATTERN = re.compile(
    r"^\s*iris\b.*\btask(?:s)?\b.*$",
    re.IGNORECASE,
)
TASK_COMPLETE_PATTERN = re.compile(
    r"^\s*(?:iris,?\s+)?(?:complete|done|cross\s+off|tick\s+off)\s+(?:task\s*)?(?:number\s*)?(?P<task_id>task-\d+|\d+)\s*[?.!]*\s*$",
    re.IGNORECASE,
)
YES_PATTERN = re.compile(
    r"^\s*(?:yes|yep|yeah|please|yes please|sure|ok|okay)\s*[!.]*\s*$",
    re.IGNORECASE,
)

VALID_STATUSES = {"Open", "In Progress", "On Hold", "Done"}
DB_PATH = os.getenv("TASK_DB_PATH", "tasks.sqlite3")
IRIS_COLOR = discord.Color(0xB072FF)
COMPLETION_PRAISES = [
    "Great work, {name}. {task_id} is crossed off.",
    "Beautifully done, {name}. {task_id} is all sorted.",
    "You're the best, babe. {task_id} is complete.",
    "Lovely work, darling. {task_id} is off the list.",
    "Thank you, {name}. {task_id} is handled.",
    "Excellent work, {name}. {task_id} is done.",
    "Nicely done, babe. {task_id} has been crossed off.",
    "Appreciate you, darling. {task_id} is complete.",
    "Clean finish, {name}. {task_id} is done and dusted.",
    "Perfect, babe. {task_id} is complete.",
    "That is one less thing on the list, darling. {task_id} is done.",
    "Gorgeous work, {name}. {task_id} is crossed off.",
    "Look at you go, babe. {task_id} is complete.",
    "Brilliant, darling. {task_id} is handled.",
    "Task handled beautifully, {name}. {task_id} is done.",
]
ADMIN_COMPLETION_PRAISES = [
    "Great work. {task_id} is crossed off.",
    "Beautifully done. {task_id} is all sorted.",
    "Thank you. {task_id} is handled.",
    "Excellent work. {task_id} is complete.",
    "Nicely done. {task_id} has been crossed off.",
    "Clean finish. {task_id} is done and dusted.",
    "Perfect. {task_id} is complete.",
    "That is one less thing on the list. {task_id} is done.",
    "Brilliant. {task_id} is handled.",
    "Task handled beautifully. {task_id} is done.",
]


@dataclass(frozen=True)
class Config:
    token: str
    task_channel_id: int
    olive_user_id: int
    elithe_user_id: int
    admin_role_ids: set[int]

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("DISCORD_TOKEN", "").strip()
        task_channel_id = _required_int("TASK_CHANNEL_ID")
        olive_user_id = _required_int("OLIVE_USER_ID")
        elithe_user_id = _required_int("ELITHE_USER_ID")
        admin_role_ids = _parse_id_set(os.getenv("ADMIN_ROLE_IDS", ""))

        if not token:
            raise RuntimeError("DISCORD_TOKEN is required.")

        return cls(
            token=token,
            task_channel_id=task_channel_id,
            olive_user_id=olive_user_id,
            elithe_user_id=elithe_user_id,
            admin_role_ids=admin_role_ids,
        )


def _required_int(name: str) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required.")
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a Discord snowflake ID.") from exc


def _parse_id_set(value: str) -> set[int]:
    ids: set[int] = set()
    for raw in value.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            ids.add(int(raw))
        except ValueError as exc:
            raise RuntimeError("ADMIN_ROLE_IDS must be a comma-separated list of role IDs.") from exc
    return ids


class TaskStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = asyncio.Lock()
        self.init_db()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    description TEXT NOT NULL,
                    requester_id INTEGER NOT NULL,
                    assignee_id INTEGER,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    last_reminded_at TEXT,
                    message_id INTEGER
                )
                """
            )
            self._ensure_column(conn, "last_reminded_at", "TEXT")

    def _ensure_column(self, conn: sqlite3.Connection, name: str, column_type: str) -> None:
        existing_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
        }
        if name not in existing_columns:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {column_type}")

    async def create_task(
        self,
        description: str,
        requester_id: int,
        assignee_id: Optional[int],
    ) -> sqlite3.Row:
        async with self._lock:
            with self.connect() as conn:
                next_number = self._next_task_number(conn)
                task_id = f"TASK-{next_number:03d}"
                conn.execute(
                    """
                    INSERT INTO tasks (
                        id, description, requester_id, assignee_id, status,
                        created_at, completed_at, last_reminded_at, message_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
                    """,
                    (
                        task_id,
                        description,
                        requester_id,
                        assignee_id,
                        "Open",
                        utcnow_iso(),
                    ),
                )
                return conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()

    async def set_task_message_id(self, task_id: str, message_id: int) -> None:
        async with self._lock:
            with self.connect() as conn:
                conn.execute(
                    "UPDATE tasks SET message_id = ? WHERE id = ?",
                    (message_id, task_id),
                )

    async def get_task(self, task_id: str) -> Optional[sqlite3.Row]:
        async with self._lock:
            with self.connect() as conn:
                return conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()

    async def update_status(
        self,
        task_id: str,
        status: str,
        assignee_id: Optional[int] = None,
        assign_if_unassigned: bool = False,
    ) -> Optional[sqlite3.Row]:
        if status not in VALID_STATUSES:
            raise ValueError(f"Unsupported status: {status}")

        async with self._lock:
            with self.connect() as conn:
                task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
                if task is None:
                    return None

                next_assignee = task["assignee_id"]
                if assign_if_unassigned and next_assignee is None:
                    next_assignee = assignee_id
                elif assignee_id is not None:
                    next_assignee = assignee_id

                completed_at = utcnow_iso() if status == "Done" else None
                conn.execute(
                    """
                    UPDATE tasks
                    SET status = ?, assignee_id = ?, completed_at = ?
                    WHERE id = ?
                    """,
                    (status, next_assignee, completed_at, task_id),
                )
                return conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()

    async def overdue_tasks(self) -> list[sqlite3.Row]:
        async with self._lock:
            with self.connect() as conn:
                return conn.execute(
                    """
                    SELECT *
                    FROM tasks
                    WHERE status != 'Done'
                      AND assignee_id IS NOT NULL
                      AND datetime(created_at) <= datetime('now', '-48 hours')
                      AND (
                        last_reminded_at IS NULL
                        OR datetime(last_reminded_at) <= datetime('now', '-48 hours')
                      )
                    ORDER BY created_at ASC
                    """
                ).fetchall()

    async def mark_reminded(self, task_id: str) -> None:
        async with self._lock:
            with self.connect() as conn:
                conn.execute(
                    "UPDATE tasks SET last_reminded_at = ? WHERE id = ?",
                    (utcnow_iso(), task_id),
                )

    async def active_tasks_for_assignee(
        self,
        assignee_id: int,
        include_unassigned: bool = False,
    ) -> list[sqlite3.Row]:
        async with self._lock:
            with self.connect() as conn:
                if include_unassigned:
                    return conn.execute(
                        """
                        SELECT *
                        FROM tasks
                        WHERE status != 'Done'
                          AND (assignee_id = ? OR assignee_id IS NULL)
                        ORDER BY created_at ASC
                        """,
                        (assignee_id,),
                    ).fetchall()

                return conn.execute(
                    """
                    SELECT *
                    FROM tasks
                    WHERE status != 'Done'
                      AND assignee_id = ?
                    ORDER BY created_at ASC
                    """,
                    (assignee_id,),
                ).fetchall()

    async def active_tasks(self) -> list[sqlite3.Row]:
        async with self._lock:
            with self.connect() as conn:
                return conn.execute(
                    """
                    SELECT *
                    FROM tasks
                    WHERE status != 'Done'
                    ORDER BY
                        CASE WHEN assignee_id IS NULL THEN 0 ELSE 1 END,
                        created_at ASC
                    """
                ).fetchall()

    def _next_task_number(self, conn: sqlite3.Connection) -> int:
        row = conn.execute(
            """
            SELECT id
            FROM tasks
            WHERE id LIKE 'TASK-%'
            ORDER BY CAST(SUBSTR(id, 6) AS INTEGER) DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return 1
        return int(row["id"].split("-", 1)[1]) + 1


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_task_message(content: str, config: Config) -> Optional[tuple[str, Optional[int], str]]:
    match = TASK_PATTERN.match(content)
    if not match:
        return None

    assignee_key = (
        match.group("name")
        or match.group("atname")
        or role_id_to_assignee(match.group("rolemention"), config)
        or user_id_to_assignee(match.group("mention"), config)
    )
    if not assignee_key:
        return None

    assignee = assignee_from_key(assignee_key, config)
    if assignee is None:
        return None

    assignee_label, assignee_id = assignee
    description = match.group("task").strip()
    return assignee_label, assignee_id, description


def assignee_from_key(assignee_key: str, config: Config) -> Optional[tuple[str, Optional[int]]]:
    assignee_key = assignee_key.lower()
    if assignee_key == "olive":
        return "Olive", config.olive_user_id
    elif assignee_key in {"elithe", "eli"}:
        return "Eli", config.elithe_user_id
    elif assignee_key == "admin":
        return "Admin", None
    return None


def user_id_to_assignee(user_id: Optional[str], config: Config) -> Optional[str]:
    if not user_id:
        return None
    parsed = int(user_id)
    if parsed == config.olive_user_id:
        return "olive"
    if parsed == config.elithe_user_id:
        return "elithe"
    return None


def role_id_to_assignee(role_id: Optional[str], config: Config) -> Optional[str]:
    if not role_id:
        return None
    if int(role_id) in config.admin_role_ids:
        return "admin"
    return None


def format_user(user_id: Optional[int]) -> str:
    return f"<@{user_id}>" if user_id else "Unassigned"


def build_task_embed(task: sqlite3.Row) -> discord.Embed:
    status = task["status"]

    description = task["description"]
    if status == "Done":
        description = f"~~{description}~~"

    embed = discord.Embed(
        title=f"{task['id']} - {status}",
        description=description,
        color=IRIS_COLOR,
        timestamp=datetime.fromisoformat(task["created_at"]),
    )
    embed.add_field(name="Requester", value=format_user(task["requester_id"]), inline=True)
    embed.add_field(name="Assignee", value=format_user(task["assignee_id"]), inline=True)
    embed.add_field(name="Status", value=status, inline=True)
    embed.add_field(name="Created", value=f"<t:{int(datetime.fromisoformat(task['created_at']).timestamp())}:f>", inline=False)
    if task["completed_at"]:
        completed = datetime.fromisoformat(task["completed_at"])
        embed.add_field(name="Completed", value=f"<t:{int(completed.timestamp())}:f>", inline=False)
        embed.set_footer(text="Task crossed off")
    return embed


def has_admin_role(member: discord.Member, config: Config) -> bool:
    if member.guild_permissions.administrator:
        return True
    return any(role.id in config.admin_role_ids for role in member.roles)


def is_task_team_member(member: discord.Member, config: Config) -> bool:
    return member.id in {config.olive_user_id, config.elithe_user_id} or has_admin_role(member, config)


def is_configured_task_channel(message: discord.Message, config: Config) -> bool:
    return message.channel.id == config.task_channel_id


def task_team_name(member: discord.Member, config: Config) -> str:
    if member.id == config.olive_user_id:
        return "Olive"
    if member.id == config.elithe_user_id:
        return "Eli"
    return member.display_name


def can_complete_task(member: discord.Member, task: sqlite3.Row, config: Config) -> bool:
    if has_admin_role(member, config):
        return True
    return task["assignee_id"] == member.id


def normalize_task_id(raw_task_id: str) -> str:
    raw_task_id = raw_task_id.strip().upper()
    if raw_task_id.startswith("TASK-"):
        number = raw_task_id.split("-", 1)[1]
    else:
        number = raw_task_id
    try:
        return f"TASK-{int(number):03d}"
    except ValueError:
        return raw_task_id


def format_task_list(tasks_to_show: list[sqlite3.Row], heading: str) -> str:
    if not tasks_to_show:
        return "No open tasks found."

    lines = [heading]
    for task in tasks_to_show:
        assignee = "Unassigned" if task["assignee_id"] is None else format_user(task["assignee_id"])
        lines.append(f"- {task['id']} [{task['status']}] {task['description']} ({assignee})")
    lines.append("")
    lines.append("To cross one off, say `complete task number TASK-001` or `complete task number 1`.")
    return "\n".join(lines)


def completion_praise(user: discord.User | discord.Member, task_id: str, config: Config) -> str:
    if user.id == config.olive_user_id:
        template = random.choice(COMPLETION_PRAISES)
        return template.format(name="Olive", task_id=task_id)
    if user.id == config.elithe_user_id:
        template = random.choice(COMPLETION_PRAISES)
        return template.format(name="Eli", task_id=task_id)

    template = random.choice(ADMIN_COMPLETION_PRAISES)
    return template.format(task_id=task_id)


class TaskButtons(discord.ui.View):
    def __init__(self, task_id: str, closed: bool = False):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="Start",
                style=discord.ButtonStyle.primary,
                custom_id=f"task:start:{task_id}",
                disabled=closed,
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Complete",
                style=discord.ButtonStyle.success,
                custom_id=f"task:complete:{task_id}",
                disabled=closed,
            )
        )
        self.add_item(
            discord.ui.Button(
                label="Hold",
                style=discord.ButtonStyle.secondary,
                custom_id=f"task:hold:{task_id}",
                disabled=closed,
            )
        )


class TaskBot(commands.Bot):
    def __init__(self, config: Config, store: TaskStore):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        intents.guilds = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        self.config = config
        self.store = store
        self.pending_task_list_confirmations: set[tuple[int, int]] = set()

    async def setup_hook(self) -> None:
        await self.add_cog(IrisSlashCommands(self))
        await self.tree.sync()
        self.reminder_loop.start()

    async def on_ready(self) -> None:
        print(f"Logged in as {self.user} ({self.user.id if self.user else 'unknown'})")

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return

        confirmation_key = (message.channel.id, message.author.id)
        if confirmation_key in self.pending_task_list_confirmations and YES_PATTERN.match(message.content):
            self.pending_task_list_confirmations.discard(confirmation_key)
            await self.send_task_list(message)
            await self.process_commands(message)
            return

        if TASK_COMPLETE_PATTERN.match(message.content):
            await self.complete_task_from_message(message)
            await self.process_commands(message)
            return

        if TASK_LIST_EVERYTHING_PATTERN.match(message.content):
            await self.send_everything_task_list(message)
            await self.process_commands(message)
            return

        if TASK_LIST_PATTERN.match(message.content):
            await self.send_task_list(message)
            await self.process_commands(message)
            return

        if FUZZY_TASK_LIST_PATTERN.match(message.content):
            await self.confirm_task_list_request(message)
            await self.process_commands(message)
            return

        parsed = parse_task_message(message.content, self.config)
        if parsed is None:
            await self.process_commands(message)
            return

        assignee_label, assignee_id, description = parsed
        created = await self.create_task_for_requester(
            description=description,
            requester_id=message.author.id,
            assignee_id=assignee_id,
        )
        if not created:
            await message.reply("Task channel is not messageable. Please check TASK_CHANNEL_ID.")
            return

        await message.reply("Thank you for your task. I will notify you once this has been completed")
        await self.process_commands(message)

    async def create_task_for_requester(
        self,
        description: str,
        requester_id: int,
        assignee_id: Optional[int],
    ) -> bool:
        task = await self.store.create_task(
            description=description,
            requester_id=requester_id,
            assignee_id=assignee_id,
        )
        task_channel = self.get_channel(self.config.task_channel_id)
        if task_channel is None:
            task_channel = await self.fetch_channel(self.config.task_channel_id)
        if not isinstance(task_channel, discord.abc.Messageable):
            return False

        task_message = await task_channel.send(
            embed=build_task_embed(task),
            view=TaskButtons(task["id"]),
        )
        await self.store.set_task_message_id(task["id"], task_message.id)
        return True

    async def send_task_list(self, message: discord.Message) -> None:
        if not isinstance(message.author, discord.Member) or not is_task_team_member(message.author, self.config):
            await message.reply("I can only show task lists to the team.")
            return
        if not is_configured_task_channel(message, self.config):
            await message.reply("Please use the task channel for that.")
            return

        active_tasks = await self.store.active_tasks_for_assignee(message.author.id)
        await message.reply(format_task_list(active_tasks, "Here are your open tasks:"))

    async def send_everything_task_list(self, message: discord.Message) -> None:
        if not isinstance(message.author, discord.Member) or not has_admin_role(message.author, self.config):
            await message.reply("Only admins can ask me to show everything.")
            return
        if not is_configured_task_channel(message, self.config):
            await message.reply("Please use the task channel for that.")
            return

        active_tasks = await self.store.active_tasks()
        await message.reply(format_task_list(active_tasks, "Here are all open tasks:"))

    async def confirm_task_list_request(self, message: discord.Message) -> None:
        if not isinstance(message.author, discord.Member) or not is_task_team_member(message.author, self.config):
            return
        if not is_configured_task_channel(message, self.config):
            await message.reply("Please use the task channel for that.")
            return

        self.pending_task_list_confirmations.add((message.channel.id, message.author.id))
        await message.reply(f"Would you like to see your tasks, {task_team_name(message.author, self.config)}?")

    async def complete_task_from_message(self, message: discord.Message) -> None:
        if not isinstance(message.author, discord.Member) or not is_task_team_member(message.author, self.config):
            await message.reply("I can only complete tasks for the team.")
            return
        if not is_configured_task_channel(message, self.config):
            await message.reply("Please use the task channel for that.")
            return

        match = TASK_COMPLETE_PATTERN.match(message.content)
        if match is None:
            return

        task_id = normalize_task_id(match.group("task_id"))
        task = await self.store.get_task(task_id)
        if task is None:
            await message.reply(f"I could not find {task_id}.")
            return
        if task["status"] == "Done":
            await message.reply(f"{task_id} is already complete.")
            return
        if not can_complete_task(message.author, task, self.config):
            await message.reply("You can only complete tasks assigned to you.")
            return

        updated = await self.store.update_status(task_id, "Done")
        await self.refresh_task_message(updated)
        await self.notify_requester_completed(updated)
        await message.reply(completion_praise(message.author, task_id, self.config))

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        data = interaction.data if isinstance(interaction.data, dict) else {}
        custom_id = str(data.get("custom_id", ""))
        if not custom_id.startswith("task:"):
            return

        parts = custom_id.split(":", 2)
        if len(parts) != 3:
            await interaction.response.send_message("Invalid task action.", ephemeral=True)
            return

        action, task_id = parts[1], parts[2]
        if action == "start":
            await self.handle_start(interaction, task_id)
        elif action == "complete":
            await self.handle_complete(interaction, task_id)
        elif action == "hold":
            await self.handle_hold(interaction, task_id)
        else:
            await interaction.response.send_message("Unknown task action.", ephemeral=True)

    async def handle_start(self, interaction: discord.Interaction, task_id: str) -> None:
        task = await self.store.get_task(task_id)
        if task is None:
            await interaction.response.send_message("That task no longer exists.", ephemeral=True)
            return
        if task["assignee_id"] is not None and task["assignee_id"] != interaction.user.id:
            await interaction.response.send_message("This task is already assigned.", ephemeral=True)
            return

        updated = await self.store.update_status(
            task_id,
            "In Progress",
            assignee_id=interaction.user.id,
            assign_if_unassigned=True,
        )
        await self.edit_task_interaction(interaction, updated, f"{task_id} is now in progress.")

    async def handle_complete(self, interaction: discord.Interaction, task_id: str) -> None:
        task = await self.store.get_task(task_id)
        if task is None:
            await interaction.response.send_message("That task no longer exists.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member) or not can_complete_task(interaction.user, task, self.config):
            await interaction.response.send_message("You can only complete tasks assigned to you.", ephemeral=True)
            return

        updated = await self.store.update_status(task_id, "Done")
        await self.edit_task_interaction(interaction, updated, completion_praise(interaction.user, task_id, self.config))
        await self.notify_requester_completed(updated)

    async def handle_hold(self, interaction: discord.Interaction, task_id: str) -> None:
        updated = await self.store.update_status(task_id, "On Hold")
        await self.edit_task_interaction(interaction, updated, f"{task_id} is now on hold.")

    async def edit_task_interaction(
        self,
        interaction: discord.Interaction,
        task: Optional[sqlite3.Row],
        acknowledgement: str,
    ) -> None:
        if task is None:
            await interaction.response.send_message("That task no longer exists.", ephemeral=True)
            return
        is_closed = task["status"] == "Done"
        await interaction.response.edit_message(
            embed=build_task_embed(task),
            view=TaskButtons(task["id"], closed=is_closed),
        )
        await interaction.followup.send(acknowledgement, ephemeral=True)

    async def refresh_task_message(self, task: Optional[sqlite3.Row]) -> None:
        if task is None or task["message_id"] is None:
            return

        task_channel = self.get_channel(self.config.task_channel_id)
        if task_channel is None:
            task_channel = await self.fetch_channel(self.config.task_channel_id)
        if not isinstance(task_channel, discord.TextChannel):
            return

        try:
            task_message = await task_channel.fetch_message(task["message_id"])
        except discord.NotFound:
            return

        await task_message.edit(
            embed=build_task_embed(task),
            view=TaskButtons(task["id"], closed=task["status"] == "Done"),
        )

    async def notify_requester_completed(self, task: Optional[sqlite3.Row]) -> None:
        if task is None:
            return
        requester = self.get_user(task["requester_id"]) or await self.fetch_user(task["requester_id"])
        try:
            await requester.send(f"Hi! Your request for {task['description']} has been completed")
        except discord.Forbidden:
            pass

    @tasks.loop(hours=48)
    async def reminder_loop(self) -> None:
        await self.wait_until_ready()
        task_channel = self.get_channel(self.config.task_channel_id)
        if task_channel is None:
            task_channel = await self.fetch_channel(self.config.task_channel_id)
        if not isinstance(task_channel, discord.abc.Messageable):
            return

        for task in await self.store.overdue_tasks():
            await task_channel.send(
                f"{format_user(task['assignee_id'])} reminder: {task['id']} is still {task['status']} after 48 hours."
            )
            await self.store.mark_reminded(task["id"])

    @reminder_loop.before_loop
    async def before_reminder_loop(self) -> None:
        await self.wait_until_ready()


class IrisSlashCommands(commands.Cog):
    def __init__(self, bot: TaskBot):
        self.bot = bot

    @app_commands.command(name="iristask", description="Create a request for Iris.")
    @app_commands.describe(
        assignee="Who should handle this request?",
        task="What needs doing?",
    )
    @app_commands.choices(
        assignee=[
            app_commands.Choice(name="Olive", value="olive"),
            app_commands.Choice(name="Eli / Elithe", value="eli"),
            app_commands.Choice(name="Admin", value="admin"),
        ]
    )
    async def iristask(
        self,
        interaction: discord.Interaction,
        assignee: app_commands.Choice[str],
        task: str,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Please use this in the server.", ephemeral=True)
            return

        assignee_info = assignee_from_key(assignee.value, self.bot.config)
        if assignee_info is None:
            await interaction.response.send_message("I do not know who that should go to.", ephemeral=True)
            return

        description = task.strip()
        if not description:
            await interaction.response.send_message("Please include the task you want logged.", ephemeral=True)
            return

        _, assignee_id = assignee_info
        created = await self.bot.create_task_for_requester(
            description=description,
            requester_id=interaction.user.id,
            assignee_id=assignee_id,
        )
        if not created:
            await interaction.response.send_message(
                "Task channel is not messageable. Please check TASK_CHANNEL_ID.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "Thank you for your task. I will notify you once this has been completed",
            ephemeral=True,
        )


def main() -> None:
    config = Config.from_env()
    store = TaskStore(DB_PATH)
    bot = TaskBot(config, store)
    bot.run(config.token)


if __name__ == "__main__":
    main()
