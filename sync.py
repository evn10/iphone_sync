#!/usr/bin/python3

import logging
import sqlite3
from os import makedirs, remove, rmdir, walk
from os.path import getmtime
from os.path import isdir
from os.path import isfile
from pathlib import Path
from shutil import copyfile
from subprocess import call
from urllib.parse import unquote, urlparse


def error_out(error_msg, db_conn=None):
    '''
    Print a message, close the Strawberry DB if open, exit with an error.
    '''
    logging.error(error_msg)
    if db_conn:
        db_conn.close()
    exit(1)


# The iOS identifier for your music app
#ios_appname = 'com.btrlabs.btramp'
ios_appname = 'org.videolan.vlc-ios'
# Names of playlists you DON'T want to sync
playlists_to_ignore = ['Unlistened', 'Test', 'All Tracks']
# The base folder where your collection is located
#src_folder = '~/Music/Download by MediaHuman'
src_folder = '/home/ake/Music'
# The folder where your iPhone will be mounted
dst_folder = '/media/iPhone'
# The folder in case if was not under a iTunes folder
iTunes_folder = "iTunes/iTunes Media/Music"
# Your Strawberry SQLite DB location (including / prefix)
music_db_file = '/.local/share/strawberry/strawberry/strawberry.db'
# Change `INFO` to `DEBUG` here for more precise troubleshooting
#logging.basicConfig(format='%(message)s', level=logging.INFO)
logging.basicConfig(format='%(message)s', level=logging.DEBUG)

logging.debug(f'Connecting to DB at $HOME{music_db_file} ...')
try:
    #home_folder_path = str(Path.home())
    home_folder_path = '/home/ake'
    db = sqlite3.connect(home_folder_path + music_db_file)
    c = db.cursor()
except Exception as e:
    error_out(f'Unable to connect to Strawberry DB: {e}', db)

logging.debug('Building queue from DB ...')
try:
    sync_queue = {}
    raw_playlist_ids = c.execute(
        """
        SELECT ROWID
        FROM playlists
        WHERE name NOT IN ({})
        """.format(','.join('?' * len(playlists_to_ignore))),
        tuple(playlists_to_ignore)
    ).fetchall()
    playlist_ids = [i[0] for i in raw_playlist_ids]

    logging.debug(f'    Found {len(playlist_ids)} playlists.')
    
    raw_playlists = c.execute(
        """
        SELECT *
        FROM playlists
        WHERE name NOT IN ({})
        """.format(','.join('?' * len(playlists_to_ignore))),
        tuple(playlists_to_ignore)
    ).fetchall()
    playlist_names = [i[0] for i in raw_playlists]
    print(playlist_names)

    for playlist_id in playlist_ids:
        playlist_songs = c.execute(
            """
            SELECT
                songs.url,
                songs.mtime
            FROM songs
            LEFT JOIN playlist_items
                ON songs.rowid = playlist_items.collection_id
            WHERE playlist_items.playlist = ?
            """,
            (playlist_id,)
        ).fetchall()
        # Add song to queue, and also convert URLs to relative links, e.g.
        # file:///path/to/artist/album/song.mp3 to artist/album/song.mp3
        for song in playlist_songs:
            #sync_queue[str(Path(unquote(urlparse(song[0]).path)).relative_to('/mnt/music'))] = song[1]
            sync_queue[str(Path(unquote(urlparse(song[0]).path)).relative_to(src_folder))] = song[1]
        logging.debug(f'    Added {len(playlist_songs)} songs from playlist #{playlist_id}.')
    db.close()
    logging.debug(f'    Found {str(len(sync_queue))} files for sync.')
except Exception as e:
    error_out(f'Unable to build queue: {e}', db)

logging.debug('Checking iPhone mount status ...')
try:
    if not Path(dst_folder).exists():
        logging.debug(f'    Creating {dst_folder} ...')
        makedirs(dst_folder)
    if not Path(dst_folder).is_mount():
        logging.debug(f'    Mounting iPhone at {dst_folder}.')
        ret = call(f'ifuse --documents {ios_appname} {dst_folder}', shell=True)
        if ret != 0:
            raise Exception(f'ifuse returned {ret}')
    else:
        logging.debug(f'    iPhone is already mounted at {dst_folder}.')
except Exception as e:
    error_out(f'Unable to mount iPhone: {e}')

logging.info('Syncing files ...')
try:
    unchanged = overwritten = removed = 0
    for (path, folders, files) in walk(dst_folder):
        for file in files:
            # Ignore BTRAmp stuff
            if file == 'PlayerLog.log':
                continue
            if not iTunes_folder in path:
                print("iTunes path not found")
                path = dst_folder + '/' + iTunes_folder + '/' + path.lstrip(dst_folder)
            dst_path = Path(path + '/' + file)
            relative_path = str(Path(path + '/' + file).relative_to(dst_folder))
            print({dst_path}, {relative_path})
            # Same filename exists in src + dst: recopy if newer
            if relative_path in sync_queue:
                if sync_queue[relative_path] > getmtime(dst_path):
                    if Path(src_folder + '/' + relative_path).exists():
                        logging.warning(f'    Overwriting older file: {relative_path}')
                        overwritten += 1
                        copyfile(Path(src_folder + '/' + relative_path), dst_path)
                    else:
                        raise Exception(f'Unable to read source file {relative_path}')
                else:
                    unchanged += 1
                    logging.debug(f'    Skipping unchanged file: {relative_path}')
                # Remove handled file from queue
                del sync_queue[relative_path]
            # Destination file isn't in the queue, delete it
            else:
                if not isdir(relative_path) and Path(dst_path).exists():
                   logging.warning(f'    Removing file: {relative_path}, {dst_path}')
                   removed += 1
                   remove(dst_path)
                else: 
                    logging.warning(f'   Either the path does not exist or this is a directory : {dst_path} {relative_path}')
            print ("destination folder", {dst_folder}, "file : ", {file})
        # Clean up empty folders; yes, there's potentially minor churn here
        if folders == [] and files == [] and '_btramp' not in path:
            if isdir(dst_path):
                logging.warning(f'    Removing folder {dst_path}')
                rmdir(dst_path)
    newsongs = len(sync_queue)
    for song in sync_queue.keys():
        #dst_path = Path(dst_folder + '/' + song)
        dst_path = Path(dst_folder + '/' + iTunes_folder + '/' + song)
        folder = dst_path.parent
        if not folder.exists():
            logging.debug(f'    {folder}')
            makedirs(folder)
        logging.debug(f'        {song}')
        logging.debug(f'        {dst_path}')
        logging.debug(f'        {dst_path.parent}')
        copyfile(Path(src_folder + '/' + song), dst_path)
    logging.info(f'    {newsongs} new, {unchanged} unchanged, {overwritten} updated, {removed} deleted.')
except Exception as e:
    error_out(f'Unable to sync files: {e}')

# logging.debug('Unmounting iPhone ...')
# try:
#      ret = call(f'fusermount -u {dst_folder}', shell=True)
#      if ret != 0:
#          raise Exception(f'fusermount returned {ret}')
# except Exception as e:
#     error_out(f'Unable to unmount iPhone: {e}')

logging.info('Sync complete.')
exit()


