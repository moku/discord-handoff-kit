"""Discord credentials for the handoff skill.

Sourced from environment variables so no secret is committed. Set them in your
shell / service manager, or in an env file that `handoff_lib` loads. See the
skill README for the resolution order (`_resolve_token`).
"""

import os

DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
DISCORD_AUTHORIZED_USER_ID = os.environ.get("DISCORD_AUTHORIZED_USER_ID", "")
