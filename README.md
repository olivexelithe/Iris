# Iris Discord Task Manager

Iris is a simple Discord task manager bot for a private team workflow. Players create requests with normal messages, Iris logs them in a private task channel for Olive, Eli, and admins, and players only get a receipt plus a completion DM.

## What Players Say

Players can create tasks with messages like:

```text
Olive can you add a music channel?
Olive, can you add a music channel?
Elithe can you fix the trivia scoring
Admin can you review this suggestion
@Olive can you check this?
@Admin can you review this?
```

Iris replies publicly:

```text
Thank you for your task. I will notify you once this has been completed
```

No task ID or private channel link is shown to the requester.

When the task is complete, Iris DMs the requester:

```text
Hi! Your request for add a music channel has been completed
```

## Staff Commands

These commands only show task details in the configured private task channel.

```text
Iris tasks
Iris show me my tasks.
Iris what tasks have I got?
Iris what tasks do I have?
Iris what are my tasks?
Iris show tasks
```

Admins can show the whole open queue:

```text
Iris show everything
```

Olive and Eli can also be admins. Iris still treats them as Olive/Eli for personal task lists and praise because their configured user IDs are checked before generic admin handling.

If Iris sees a staff message that mentions her and tasks but does not quite match, she asks:

```text
Would you like to see your tasks, Eli?
```

Reply with `yes` and she will show them in the task channel.

## Completing Tasks

Tasks can be completed with the button on the task embed, or by saying:

```text
complete task 3
complete task number 3
Iris complete task 3
Iris complete TASK-003
done task 3
cross off task 3
tick off task 3
```

Olive and Eli can complete tasks assigned to themselves. Admins can complete any task.

When a task is completed, Iris praises the person who completed it with a rotating message, updates the task embed, crosses out the description, disables the buttons, and DMs the requester.

Olive and Eli get named praise with warmer variants like:

```text
Great work, Olive. TASK-003 is crossed off.
You're the best, babe. TASK-004 is complete.
Lovely work, darling. TASK-005 is off the list.
```

Other admins get simpler praise without names:

```text
Great work. TASK-006 is crossed off.
Excellent work. TASK-007 is complete.
```

## Features

- Normal message-based task creation, no slash commands needed
- Case-insensitive matching
- Optional comma support, like `Olive, can you ...`
- Real user mention support for Olive and Elithe
- Admin role mention support through configured admin role IDs
- SQLite task storage
- Task IDs like `TASK-001`, `TASK-002`
- Private task channel embeds
- Start, Complete, and Hold buttons
- Text completion commands like `complete task 3`
- 48-hour overdue reminders for assigned unfinished tasks
- Player-facing acknowledgement and completion DM

## Files

```text
bot.py
requirements.txt
Procfile
.env.example
.gitignore
README.md
```

## Environment Variables

Copy `.env.example` as a reference. Do not commit a real `.env` file.

```text
DISCORD_TOKEN=your-bot-token
TASK_CHANNEL_ID=123456789012345678
ADMIN_ROLE_IDS=123456789012345678,234567890123456789
OLIVE_USER_ID=345678901234567890
ELITHE_USER_ID=456789012345678901
TASK_DB_PATH=tasks.sqlite3
```

`ADMIN_ROLE_IDS` can be empty if you only want Discord server administrators to count as admins.

## Discord Bot Setup

In the Discord Developer Portal, enable these privileged gateway intents:

- Message Content Intent
- Server Members Intent

The bot also needs permissions to:

- Read messages
- Send messages
- Embed links
- Use external emojis is not required
- Read message history
- Send DMs to users, if users allow DMs from the server

## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Set your environment variables:

```powershell
$env:DISCORD_TOKEN="your-bot-token"
$env:TASK_CHANNEL_ID="123456789012345678"
$env:ADMIN_ROLE_IDS="123456789012345678,234567890123456789"
$env:OLIVE_USER_ID="345678901234567890"
$env:ELITHE_USER_ID="456789012345678901"
$env:TASK_DB_PATH="tasks.sqlite3"
```

Run Iris:

```powershell
python bot.py
```

## Database

You do not need to manually create a database.

Iris uses SQLite and creates the database file automatically on startup. By default it creates:

```text
tasks.sqlite3
```

It also creates the `tasks` table automatically.

## Railway Notes

On Railway, you have two choices.

For quick testing, you can leave `TASK_DB_PATH=tasks.sqlite3`. Iris will create the SQLite database automatically. The downside is that Railway deployments may not preserve that file forever.

For a real live bot, add a Railway volume and set:

```text
TASK_DB_PATH=/data/tasks.sqlite3
```

Mount the volume at:

```text
/data
```

That lets the SQLite database persist across restarts and redeploys.

Railway should use the included `Procfile`:

```text
worker: python bot.py
```

Set the same environment variables in Railway's Variables tab.

## GitHub Upload

Before uploading, make sure you do not commit:

- `.env`
- `tasks.sqlite3`
- `__pycache__/`

Those are already covered by `.gitignore`.
