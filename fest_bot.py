from selenium import webdriver
import chromedriver_binary
import time
import os
import discord
import re

TOKEN = os.getenv("FESTBOT_TOKEN")

# list of emojis used by the bot. don't mess with this.
auto_emojis = ['⬆️', '↔️', '⬇️']
dead_emoji = '💀'

# broke down the artists into subgroups based on first initial
# this is because discord sucks at long scroll backs
# the values() are regex's for matching bands to the given key()
# may be worth automating the creation of each channel and setting permissions and shit, but who cares
channel_filter = {
    '0-c': '[0-9,A-C]|\W',
    'd-g': '[D-G]',
    'h-m': '[H-M]',
    'n-r': '[N-R]',
    's-t': '[S-T]',
    'u-z': '[U-Z]'
}


def get_headless_chromedriver():
    # get an instance of chrome driver without a gui and without retrieving any graphics
    chrome_options = webdriver.ChromeOptions()
    prefs = {"profile.managed_default_content_settings": {"images": 2},
             "profile.default_content_settings": {"images": 2}}
    chrome_options.experimental_options["prefs"] = prefs
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument("--blink-settings=imagesEnabled=false")
    return webdriver.Chrome(options=chrome_options)


def get_channel_based_on_id(this_client, channel_name):
    """ grab channel id given the channel name """
    y = [x for x in this_client.get_all_channels() if x.name == channel_name]
    if len(y) == 1:
        return y[0]
    else:
        return None


def get_band_names():
    """ use chromedriver to grab the current roster from the fest website """

    driver = get_headless_chromedriver()

    band_names = []

    # load web html
    driver.get('https://thefestfl.com/bands')
    time.sleep(5)

    # for debug -- get the raw xml tree
    # from lxml import html, etree
    # htmldoc = html.fromstring(driver.page_source)
    # etree.tostring(htmldoc)

    # yea this is hacky AF, but what're you gonna do
    tags = driver.find_elements_by_xpath('/html/body/div/div/div/div/main/div/div/div/ul/li/button/span/span/div/div')
    for tag in tags:
        if tag.get_attribute('class') == "PerformerList__PerformerName-bon0a2-6 AzOzc":
            # convert "THE BAND" to "BAND, THE"
            if tag.text[0:3] == "THE":
                band_names.append("%s, THE" % tag.text[4:])
            else:
                band_names.append(tag.text)

    driver.close()
    band_names.sort()

    return band_names


async def update_channels(client, list_artists_from_web):
    """ update text channels with the current roster (removes unrostered artists, and adds newly rostered artists) """

    added_ct = 0
    removed_ct = 0

    for channel_id in channel_filter.keys():
        channel = get_channel_based_on_id(client, channel_id)
        # go thru list and search for matches based on channel filter
        rostered_artists = [e for e in list_artists_from_web
                                     if re.match(re.compile(channel_filter[channel_id]), e)]

        # which artists are already listed
        existing_artists = []
        async for msg in channel.history(limit=1000):
            # remove artists that aren't on the current roster
            existing_artist = msg.content
            if existing_artist not in rostered_artists:
                if dead_emoji not in [reacts.emoji for reacts in msg.reactions]:
                    await msg.add_reaction(dead_emoji)
                    removed_ct += 1
            else:
                existing_artists.append(msg.content)

        # add not yet listed artists
        for artist in rostered_artists:
            if artist not in existing_artists:
                new_msg = await channel.send(artist)
                for emoji in auto_emojis:
                    await new_msg.add_reaction(emoji)
                added_ct += 1

    return added_ct, removed_ct


async def clear_all(client):
    """ remove all artists from the text channels. basically a big reset switch. """

    for channel_id in channel_filter.keys():
        channel = get_channel_based_on_id(client, channel_id)

        async for msg in channel.history(limit=1000):
            await msg.delete()

    return


def get_spotify_url(artist):
    return "https://open.spotify.com/search/%s" % "%20".join(artist.split(' '))


async def get_artists_liked_by(name):
    """ generates a string of artists that were liked by the user """

    liked_artists = ""

    # go thru each channel from the global filter
    for channel_id in channel_filter.keys():
        channel = get_channel_based_on_id(client, channel_id)

        # scan thru the channel history to look for messages that have more than
        # 1 react of the appropriate type (1st belongs to the bot)
        async for msg in channel.history(limit=1000):
            if msg.reactions[0].count > 1:
                # grab list of users that have reacted
                users = await msg.reactions[0].users().flatten()
                # if the user of interest has reacted, then we'll add this artist to the output
                if name in [user.name for user in users]:
                    spotify_url = get_spotify_url(msg.content)
                    liked_artists = "%s\n%s (%s)" % (liked_artists, msg.content, spotify_url)

    return "You liked these artists:\n%s" % liked_artists


async def get_artists_unsure(name):
    """ generates a string of artists that were marked as "unsure" by the user """

    unsure_artists = ""

    for channel_id in channel_filter.keys():
        channel = get_channel_based_on_id(client, channel_id)

        async for msg in channel.history(limit=1000):
            if msg.reactions[1].count > 1:
                users = await msg.reactions[1].users().flatten()
                if name in [user.name for user in users]:
                    spotify_url = get_spotify_url(msg.content)
                    unsure_artists = "%s\n%s (%s)" % (unsure_artists, msg.content, spotify_url)

    return "You gave a rating of ↔️ to the following artists:\n%s" % unsure_artists


async def get_next_unrated_artists(name):
    """ generates a string of the first five encountered artists that have not yet been rated by the user """

    next_artists = ""
    next_artist_count = 0

    for channel_id in channel_filter.keys():
        channel = get_channel_based_on_id(client, channel_id)

        async for msg in channel.history(limit=1000):
            users_nested = [await reacts.users().flatten() for reacts in msg.reactions]
            users = [user for sublist in users_nested for user in sublist]
            if name not in [user.name for user in users]:
                spotify_url = get_spotify_url(msg.content)
                next_artists = "%s\n%s (%s)" % (next_artists, msg.content, spotify_url)
                next_artist_count += 1
                if next_artist_count > 5:
                    return next_artists

    return "Next unrated artists:\n%s" % next_artists


async def send_large_message(send_cmd, long_string):
    """ breaks very long messages (>2000 characters) into smaller sub messages on \n """

    single_strings = long_string.split('\n')
    chunks = []

    current_chunk = ""
    for s in single_strings:
        if len(current_chunk) + len(s) + 1 < 2000:
            current_chunk += s + "\n"
        else:
            chunks.append(current_chunk)
            current_chunk = s + "\n"
    chunks.append(current_chunk)

    for chunk in chunks:
        await send_cmd(chunk)

    return


def get_help_msg():
    help_msg = """
Hello. I am TheFest19_BOT! Here are my commands:
    !help: print this message
    !update: (admin only) pull data from thefestfl.com and update the roster
    !liked: I'll DM you the artists that you rated ⬆️.
    !unsure: I'll DM you the artists that you rated  ↔️.
    !next: I'll DM you the next few artists that you should check out.

I'll try to add some stuff about schedules/notifications later.
    """
    return help_msg


# needed to monitor new members to the channel
intents = discord.Intents.default()
intents.members = True
client = discord.Client(intents=intents)


@client.event
async def on_ready():
    print('We have logged in as {0.user}'.format(client))


@client.event
async def on_member_join(member):
    help_msg = get_help_msg()
    await member.send(help_msg)


@client.event
async def on_message(message):

    # only accept mcommands from DM or thru the bot_commands channel
    if message.channel.type == discord.ChannelType.text:
        if message.channel.name != 'bot_commands':
            return

    if message.content.startswith('!update'):
        # get updated bands list
        rostered_artists = get_band_names()

        # check current listed bands, and update channels
        added, removed = await update_channels(client, rostered_artists)
        await message.channel.send('Update complete: artists added/removed: (%d, %d)' % (added, removed))

    if message.content.startswith('!clearall'):
        # clear all artists from the text chats
        if message.author.name == 'kirrrbbby':
            await clear_all(client)
            await message.channel.send('Clear complete')

    if message.content.startswith('!liked'):
        # returns liked artists to the user
        liked_artists = await get_artists_liked_by(message.author.name)
        await send_large_message(message.channel.send, liked_artists)

    if message.content.startswith('!next'):
        # returns the next 5 unrated bands to the user
        next_artists = await get_next_unrated_artists(message.author.name)
        await send_large_message(message.channel.send, next_artists)

    if message.content.startswith('!unsure'):
        # returns artists that were rated as "unsure" to the user
        unsure_artists = await get_artists_unsure(message.author.name)
        await send_large_message(message.channel.send, unsure_artists)

    if message.content.startswith('!help'):
        # return the help message
        help_msg = get_help_msg()
        await message.channel.send(help_msg)


client.run(TOKEN)
