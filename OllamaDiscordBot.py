import discord
from discord.ext import commands
import json
import requests
import asyncio
import logging

# --- Configuration Loading ---
try:
    with open("config.json", 'r') as f:
        config = json.load(f)
    DISCORD_TOKEN = config['discord_token']
    API_URL = config['api_url']
    PREFIX = config['prefix']
    OLLAMA_MODEL = config.get('ollama_model', 'llama3.2:3b') # Default to llama3.2:3b if not specified
    HOSTED_BY = config.get('hosted_by')
    VERSION = config.get('version')
except FileNotFoundError:
    print("Error: config.json not found. Please create it.")
    exit()
except KeyError as e:
    print(f"Error: Missing key in config.json: {e}")
    exit()
except json.JSONDecodeError:
    print("Error: config.json is not valid JSON.")
    exit()

# --- Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True  # Necessary to read message content

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None) # Disable default help

# --- State Management (Stateless) ---
# Dictionary to store language preference per user {user_id: language_instruction}
user_languages = {}
SHORT_ANSWER_PROMPT = "Give me a short answer but don't mention that I've asked you to make it short." # This is so the AI doesn't exceeds the discord message lenght limit.'

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s:%(levelname)s:%(name)s: %(message)s')
logger = logging.getLogger('discord')

# --- Ollama API Interaction ---
async def call_ollama_api(prompt: str):
    """Sends a prompt to the Ollama API and returns the response content."""
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False # We want the full response at once
    }
    headers = {"Content-Type": "application/json"}

    try:
        # Run synchronous requests call in an executor to avoid blocking asyncio loop
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,  # Use default executor
            lambda: requests.post(API_URL, json=payload, headers=headers, timeout=120) # Increased timeout
        )
        response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)

        response_data = response.json()

        # Extract content - Structure depends on Ollama API version/endpoint
        # Common structure for /api/chat:
        if 'message' in response_data and 'content' in response_data['message']:
            return response_data['message']['content'].strip()
        # Fallback for other potential structures (e.g., /api/generate might use 'response')
        elif 'response' in response_data:
             return response_data['response'].strip()
        else:
            logger.error(f"Unexpected API response structure: {response_data}")
            return "Error: Received an unexpected response format from the AI."

    except requests.exceptions.Timeout:
        logger.error(f"API request timed out to {API_URL}")
        return "Error: The request to the AI timed out."
    except requests.exceptions.RequestException as e:
        logger.error(f"API request failed: {e}")
        return f"Error: Could not connect to the AI service at {API_URL}. Details: {e}"
    except json.JSONDecodeError:
        logger.error(f"Failed to decode JSON response from API. Response text: {response.text[:200]}...") # Log snippet
        return "Error: Received an invalid response from the AI."
    except KeyError as e:
        logger.error(f"Missing expected key in API response: {e}. Response data: {response_data}")
        return "Error: Received an incomplete response format from the AI."
    except Exception as e:
        logger.exception("An unexpected error occurred during API call") # Log full traceback
        return f"An unexpected error occurred while contacting the AI. Details: {e}"


# --- Bot Events ---
@bot.event
async def on_ready():
    """Event triggered when the bot is ready and logged in."""
    print(f'Logged in as {bot.user.name} ({bot.user.id})')
    print(f'Command Prefix: {PREFIX}')
    print('------')

@bot.event
async def on_command_error(ctx, error):
    """Handles errors during command processing."""
    if isinstance(error, commands.CommandNotFound):
        # Optionally, you can inform the user the command doesn't exist
        # await ctx.send(f"Sorry, I don't recognize that command. Try `{PREFIX}help`.")
        pass # Or just ignore unknown commands silently
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"You missed an argument for the `{ctx.command}` command. Check `{PREFIX}help {ctx.command}`.")
    elif isinstance(error, commands.CommandInvokeError):
        logger.error(f"Error invoking command {ctx.command}: {error.original}")
        await ctx.send("An error occurred while running this command.")
    else:
        logger.error(f"Unhandled command error: {error}")
        await ctx.send("An unexpected error occurred.")


# --- Bot Commands ---
@bot.command(name='ask')
async def ask_ollama(ctx, *, prompt_text: str):
    """Sends your prompt to the Ollama AI."""
    user_id = ctx.author.id
    language_instruction = user_languages.get(user_id, "") # Get language pref, default to "" (English/model default)

    # Construct the final prompt
    full_prompt = f"{language_instruction} {SHORT_ANSWER_PROMPT} {prompt_text}".strip()
    logger.info(f"User {ctx.author} prompted: '{prompt_text}' -> Sending: '{full_prompt}'")

    async with ctx.typing(): # Show "Bot is typing..."
        response_text = await call_ollama_api(full_prompt)

    # Send the response (handle potential length issues)
    if len(response_text) > 2000:
        await ctx.send(response_text[:1997] + "...") # Discord message limit
    elif not response_text: # Handle empty responses
        await ctx.send("The AI didn't provide a response.")
    else:
        await ctx.send(response_text)

@bot.command(name='language')
async def set_language(ctx, *, language_name: str):
    """Sets the preferred language for the AI's responses for you."""
    user_id = ctx.author.id
    if language_name.lower() in ['default', 'reset', 'english', 'en', '']:
        if user_id in user_languages:
            del user_languages[user_id]
        instruction = ""
        await ctx.send("Okay, I'll use the default language (English) for your prompts.")
        logger.info(f"User {ctx.author} reset language preference.")
    else:
        instruction = f"Answer me in {language_name}."
        user_languages[user_id] = instruction
        await ctx.send(f"Okay, I will write my answers in {language_name} for your future prompts.")
        logger.info(f"User {ctx.author} set language preference to: {language_name}")

@bot.command(name='help')
async def custom_help(ctx):
    """Displays this help message."""
    embed = discord.Embed(
        title="Ollama Bot Help",
        description=f"I can answer your questions using an Ollama AI model. My command prefix is `{PREFIX}`.",
        color=discord.Color.blue()
    )
    embed.add_field(
        name=f"{PREFIX}ask <your question>",
        value="Asks the AI your question. The AI is instructed to give short answers.",
        inline=False
    )
    embed.add_field(
        name=f"{PREFIX}language <language name>",
        value="Tells the AI to try and respond in the specified language for your future prompts (e.g., `spanish`, `french`, `japanese`).",
        inline=False
    )
    embed.add_field(
        name=f"{PREFIX}language default",
        value="Resets your language preference to the default (English).",
        inline=False
    )
    embed.add_field(
        name=f"{PREFIX}help",
        value="Shows this help message.",
        inline=False
    )
    embed.add_field(
        name="Instance Informations",
        value=f"Using model: {OLLAMA_MODEL} hosted by {HOSTED_BY}",
        inline=False
    )
    embed.add_field(
        name=f"Ollama Discord Bot | Version: {VERSION}",
        value="github.com/Nakildias/OllamaDiscordBot",
        inline=False
    )
#    embed.set_footer(text="")
    await ctx.send(embed=embed)


# --- Run the Bot ---
if __name__ == "__main__":
    if DISCORD_TOKEN == "YOUR_DISCORD_BOT_TOKEN_HERE" or not DISCORD_TOKEN:
        print("Error: Please set your DISCORD_TOKEN in config.json")
    else:
        try:
            bot.run(DISCORD_TOKEN)
        except discord.LoginFailure:
            print("Error: Invalid Discord Token. Please check config.json.")
        except Exception as e:
            print(f"An error occurred while starting the bot: {e}")
