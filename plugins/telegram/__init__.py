"""Telegram, a channel to Atlas: the plugin's `register`.

It imports from Atlas only `atlas.plugins.sdk`, as every plugin must; the rest is its own.
"""

from atlas.plugins.sdk import PluginContext

from .channel import TelegramChannel

#: The vault name of the bot's token, as the manifest declares it.
TOKEN_SECRET = "TELEGRAM_BOT_TOKEN"  # noqa: S105 - a name, not the token


def register(ctx: PluginContext) -> None:
    def make() -> TelegramChannel:
        return TelegramChannel(ctx.secret(TOKEN_SECRET), ctx.setting("api_url"), logger=ctx.logger)

    ctx.register_channel("telegram", make)
