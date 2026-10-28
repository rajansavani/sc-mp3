# Release QA checklist

Run this on each release candidate before it's published. It takes about an hour, plus the time for Spotify on the phones. Note the version, the date, the computer and the result of each step. A step passes only when everything under "You should see" happens.

The steps are for Windows. On macOS, read Cmd for Ctrl and Finder for Explorer.

## Before you start

- **A clean profile.** Use a Windows user account where sc-mp3 has never run, or delete its folder, `%LOCALAPPDATA%\sc-mp3`. On macOS, delete `~/Library/Application Support/sc-mp3`, `~/Library/Caches/sc-mp3` and `~/Library/Logs/sc-mp3`.
- **Spotify.** Have the Spotify desktop app installed and signed in. For section 14, have an iPhone and an Android phone with Spotify, signed in to the same account.
- **An image file**, JPEG or PNG, at least 600 × 600, to use as a cover.
- **A spare folder** for the disk test in section 7, such as `C:\sc-mp3-qa\locked`.
- **A tag viewer** for sections 6 and 12, such as Mp3tag, or a file's Properties › Details in Explorer.
- **These public tracks.** They've worked before. If one has gone, pick another track that fits the same description.

| Name | Link | Why |
|---|---|---|
| The Following | https://soundcloud.com/the80m/the-following | A plain track with an AAC stream |
| Superhero | https://soundcloud.com/nocopyrightsounds/unknown-brain-superhero-feat-chris-linton-ncs-release | A featured artist, the artist in the title, and promo text to clean up |
| Sideways | https://soundcloud.com/garyvee/sideways-prod-mad-real | The uploader's profile picture as its cover |
| Burn Down | https://soundcloud.com/thesilverlakes/burn-down-demo-2008 | An old MP3 upload with no cover |
| Traffic | https://soundcloud.com/ashburyheightsofficial/traffic-tcp-demo-2008-1 | Copy-protected by its label |
| Look What You Made Me Do | https://soundcloud.com/taylorswiftofficial/look-what-you-made-me-do | Go+ only, so only a 30-second preview without Go+ |
| A missing track | https://soundcloud.com/the80m/this-track-does-not-exist | A link that leads nowhere |
| out of spite | https://soundcloud.com/leviryan/sets/out-of-spite | An album of 8 tracks |
| A playlist | https://soundcloud.com/non-site_records/sets/tilt-brass-bowery-aug03_ns-src02 | A set that isn't an album |

## 1. Install and first start

1. Download the release zip from the Releases page, unzip it, and double-click `sc-mp3.exe`.
   - You should see: Windows SmartScreen warns that the app is unrecognized, since it isn't signed. More info, then Run anyway, opens it.
2. Watch the window open.
   - You should see: a dark window within a few seconds, with no white flash. It opens on "Step 1 of 4", "Where your songs go", with the folder `Music › sc-mp3`.
3. Select Next.
   - You should see: "How titles look", with three cards that each show the same sample title in a different style. "In the artist field" is picked.
4. Select "feat. in the title", then press the Left arrow key.
   - You should see: the arrow keys move the choice between the cards, and "In the artist field" is picked again.
5. Select Next.
   - You should see: "SoundCloud account (optional)", with the table of what signing in gets you, and Skip for now.
6. Select Skip for now.
   - You should see: "Add the folder to Spotify", with three numbered steps that name your folder.
7. Select Open Spotify.
   - You should see: the Spotify app opens or comes to the front.
8. Select Finish.
   - You should see: the main window, with "Paste a SoundCloud or YouTube link to start" and "Sets and albums work too", and an empty queue that says "Nothing queued yet. Links you add show up here, and unsaved ones stay after you quit."
9. Select Spotify help, in the middle of the empty window, then press Esc.
   - You should see: the Spotify help panel opens on the right, on "On your computer", and Esc closes it.

## 2. Paste, review and save

1. Copy The Following's link. Click an empty part of the window and press Ctrl+V.
   - You should see: a queue item that says "Fetching info…", then within a few seconds "Needs review" with a percentage while the audio downloads, then "Needs review · audio ready". The editor shows the title, artist, cover and "Cleaned up from the SoundCloud title".
2. Read the Output strip at the bottom.
   - You should see: "Saves as" `80M/80M - The Following.mp3`, since each artist gets a folder of their own, then the folder `Music › sc-mp3`, "AAC 160 kbps stream → MP3 320 kbps" and "Tags and cover embedded".
3. Select Save to library, or press Enter.
   - You should see: a green "Saved to Music › sc-mp3 › 80M" banner with Show in folder and Open Spotify.
4. Select Show in folder.
   - You should see: Explorer opens with the new MP3 selected.

## 3. Edits and cleanups

1. Paste the Superhero link into the top field and select Add. Wait for Needs review.
   - You should see: the title "Superhero" and the artist "Unknown Brain, Chris Linton". Under the original title in quotes, three chips: "Took the artist “Unknown Brain” from the title", "Chris Linton credited as featured artist" and "Removed “[NCS Release]”".
2. In the Year field, type `20x4` and press Tab.
   - You should see: the field turns red with "Use a year from 1 to 9999." The year isn't saved.
3. Change the year to `2016` and the title to something of your own, then press Tab.
   - You should see: both stay, and Reset appears next to each label. The queue item shows the new title.
4. Select Reset next to Title.
   - You should see: the suggested title comes back.
5. Under "Featured artists go", select each choice in turn.
   - You should see: "feat. in title" gives "Superhero (feat. Chris Linton)" by "Unknown Brain". "All artists in title" gives "Superhero (Unknown Brain, Chris Linton)". "In the artist field" goes back to step 1's. "Saves as" follows each time.
6. Select the "Took the artist" chip.
   - You should see: the chip turns to a dashed outline, "Kept “Unknown Brain” in the title", and the title starts with "Unknown Brain - " again. Selecting it again undoes that.
7. Save it.
   - You should see: it saves with your year and the style you left on.

## 4. Covers

1. Paste the Sideways link and wait for Needs review.
   - You should see: a note under the cover saying it's garyvee's profile picture, not cover art.
2. Select Replace, type `https://soundcloud.com/` in Image link, and select Use this link.
   - You should see: a red note under the cover: "That link doesn't lead to an image." The cover doesn't change.
3. Drag your image file from Explorer onto the cover.
   - You should see: it becomes the cover, captioned "From an image you added", and the red note goes away.
4. Select Crop. Drag the square, or use the arrow keys and + and −, then select Use this crop.
   - You should see: the preview and the queue thumbnail show the crop.
5. Copy an image (for example, from a browser), select Replace, and press Ctrl+V.
   - You should see: the pasted image becomes the cover.
6. Select Replace, then Use the original cover.
   - You should see: the profile picture again, with its note.
7. Put your own image back, crop it, and save.
   - You should see: it saves. Check the cover in section 13.

## 5. Skip, cancel and retry

1. Paste the Burn Down link. When it's in review, select Skip.
   - You should see: "Skipped. Nothing was saved, and the downloaded audio was deleted." with Remove from the queue.
2. Select Remove from the queue.
   - You should see: the item leaves the queue, and another item is selected.
3. In Settings, turn off "Download while I review" and go back. Paste the Burn Down link again, and select Save to library as soon as it's in review.
   - You should see: "Downloading · N%", Cancel, and a "Saves when the download finishes" button. The queue item says "Downloading · saves when done".
4. Select Cancel.
   - You should see: the review again, with Save to library.
5. Turn "Download while I review" back on, read the Output strip, then save it.
   - You should see: "MP3 128 kbps stream, copied without re-encoding", since it's an MP3 upload, and "Tags embedded, no cover", since it has none. Then the green Saved banner.
6. Select Clear finished at the top of the queue. Turn off Wi-Fi or unplug the network, then paste The Following again.
   - You should see: "You're offline." with Retry now. sc-mp3 tries again by itself every 30 seconds.
7. Turn the network back on and select Retry now.
   - You should see: it reaches Needs review.

## 6. Duplicates and taken file names

1. Look at The Following from step 5.7.
   - You should see: a yellow banner, "Already in your library, saved <date>", with the saved file's path, Show it and Save again.
2. Select Save to library.
   - You should see: a dialog, "A file with this name is already in your library", with the path and Skip this track, Replace and Keep both.
3. Press Esc, then select Save to library again.
   - You should see: Esc closes the dialog and leaves the item in review. Saving again asks again.
4. Select Keep both.
   - You should see: it saves as "… (2).mp3" next to the first one.
5. Clear finished, paste the link again, and save. Before answering, make the first file read-only: in Explorer, its Properties, Read-only. Then select Replace.
   - You should see: "The file is open in another program or is read-only. Close it and try again." with Retry. The queue item says "File in use".
6. Clear Read-only in the file's Properties, and select Retry.
   - You should see: it saves over the first file, and no "(3)" file appears. In the tag viewer, the first file has this save's tags.

## 7. Errors

1. Paste the Traffic link.
   - You should see: "This track is copy-protected by its label, so it can't be saved." with Remove from the queue. No file is saved.
2. Paste the Look What You Made Me Do link.
   - You should see: "Only a 30-second preview is available: this is a Go+ track." with Sign in with Go+ and Remove from the queue.
3. Paste the missing track's link.
   - You should see: "This track is private or was removed." with Remove from the queue.
4. Paste `https://soundcloud.com/the80m`, a profile. Then a set, `https://soundcloud.com/the80m/sets/insomnia-ep`, and a YouTube link.
   - You should see: a toast for each, and nothing queued: "This SoundCloud link isn't a track. Open the track and copy its link.", "SoundCloud sets and albums can't be saved yet. Paste each track's own link instead." and "YouTube links can't be saved yet. Paste a SoundCloud track link instead."
5. In Settings, choose the spare folder as the library, and save The Following there. Then deny yourself write access to the folder: in its Properties, Security, Edit, add your user with Write denied. Save a track you haven't saved before.
   - You should see: "Can't save to <folder>. Check that the drive is connected, or choose another folder." with Choose another folder, Retry and Remove from the queue.
6. Paste The Following again, save it, and answer Replace.
   - You should see: the same "Can't save to <folder>" message, not "The file is open in another program".
7. Select Choose another folder and pick a folder you can write to.
   - You should see: it saves there. Set the library folder back afterwards, and remove the deny.

## 8. Drops, the clipboard and the keyboard

1. Drag a SoundCloud link from the browser's address bar onto the top field.
   - You should see: it's added like a paste.
2. Drag an MP3 file from Explorer onto the window.
   - You should see: a queue item named after the file, "Re-tag existing file". Section 13 checks re-tagging itself. Remove it from the queue.
3. From an Explorer search, drag two files with the same name from different folders onto the window.
   - You should see: one toast, without a path: "Two of the dropped files have the same name, so sc-mp3 can't tell them apart. Add them one at a time."
4. Copy a track link in the browser, then click on the sc-mp3 window.
   - You should see: "Add the link on your clipboard?" with the link, Not now and Add it. Not now hides it for that link. Add it queues it. A link that's queued already, a profile, a set or a YouTube link isn't offered.
5. Use ↑ and ↓ outside the text fields, then press Enter on a queue item, then Enter again.
   - You should see: the arrows move through the queue, the first Enter puts the cursor in the Title field, and the second saves.
6. Press Ctrl+, (Ctrl and comma).
   - You should see: Settings opens.

## 9. Settings and restarts

1. In Settings, change the Comment tag to Nothing, turn off "Offer links from the clipboard", and change "Downloads at once" to 3.
   - You should see: each change saves at once, with no error banner.
2. Quit sc-mp3 and open it again.
   - You should see: the main window, not setup, and Settings shows the three changes. Put them back.
3. In Settings, under Library, select Change and pick `%LOCALAPPDATA%\sc-mp3\Cache`.
   - You should see: "Choose a folder outside sc-mp3's own folders." The library folder doesn't change.

## 10. Spotify help and diagnostics

1. Open Settings, then Spotify help.
   - You should see: the panel on the right with three tabs: On your computer, On your phone, Songs not showing up. The arrow keys move between the tabs, and Esc closes it.
2. On "On your phone", read the iPhone part.
   - You should see: it says to copy the song files themselves into On My iPhone › Spotify, not the artist folders.
3. On "Songs not showing up", select Open the folder with local-files.bnk.
   - You should see: Explorer opens on Spotify's folder for your account, with `local-files.bnk` selected. This works for the Spotify installer version and the Microsoft Store version. Without Spotify, a toast says sc-mp3 couldn't find its folder.
4. In Settings › Engine and updates, select Copy diagnostics, and paste into Notepad.
   - You should see: "The diagnostics are on your clipboard." The text has the app and engine versions, the OS, the settings and the latest log lines. Folders show only their last part, and your Windows user name appears nowhere.

## 11. Closing

1. Turn off "Download while I review", paste a track and select Save to library. While it says "Saves when the download finishes", paste another track and leave it in review. Close the window.
   - You should see: it closes at once, without asking.
2. Open sc-mp3 again.
   - You should see: both tracks in the queue, both in review. The first one's save didn't finish, so it waits for Save again.
3. Turn "Download while I review" back on, and wait until both tracks say "Needs review · audio ready". Close the window and open sc-mp3 again.
   - You should see: both tracks back at once with "Needs review · audio ready", and no download progress.
4. Save or skip every track, or remove it from the queue. Then paste the Traffic link, wait for its error, and close the window. Open sc-mp3 again.
   - You should see: it closes without asking, and Traffic comes back with its error. Clear finished removes it.
5. Open the log folder, `%LOCALAPPDATA%\sc-mp3\Logs`, and search `sc-mp3.log` for "Traceback" and for your user name.
   - You should see: neither. Your home folder appears as `~`.

## 12. Sets and albums

1. Paste the out of spite link.
   - You should see: one queue item, "out of spite", "Levi Ryan · SoundCloud set · 8 tracks", that counts "Fetching track info · 3 of 8" and so on. The editor shows "Save as" with Album picked, "SoundCloud marks this set as an album.", the album's cover and fields, and a row for each track, numbered 1 to 8.
2. Untick track 2.
   - You should see: its row says "Left out" and loses its number. The tracks after it move up one, and the button says "Save 7 selected". Tick it again.
3. Change track 1's title in the table, then open track 1's Cleanups.
   - You should see: the cleanups and the original title in a small panel beside the row. Turn one cleanup off, if it has any, and press Esc.
4. Select another item in the queue, then select out of spite again.
   - You should see: track 1 keeps the title you typed and the cleanup you turned off.
5. Change Album to "out of spite (QA)", then select Save all 8 tracks.
   - You should see: "Saving 1 of 8 tracks…" counting up, with Cancel, then a green "Saved 8 tracks to Music › sc-mp3 › Levi Ryan" banner. The album's fields and the Album and Singles switch can't change any more.
6. Open the files in the tag viewer.
   - You should see: each has the album "out of spite (QA)", the album artist "Levi Ryan", the track numbers 1/8 to 8/8, and the same cover.
7. Paste the playlist link and wait for its tracks.
   - You should see: "Save as" with Singles picked, "SoundCloud lists this set as a playlist.", no album fields, and no track numbers. Select Album, and the numbers and album fields appear. Select Singles again.
8. Select Save all, then close the window while it saves, and open sc-mp3 again.
   - You should see: the playlist comes back with its tracks. The saved ones say "Saved", and the rest wait in review with their audio. Save them.
9. Select Clear finished.
   - You should see: both sets leave the queue.

## 13. Re-tag files you already have

Use copies of two songs you have, one MP3 and one M4A, in a folder of their own outside the library folder. Pick songs whose titles have something to clean up, such as "(Official Audio)".

1. With the queue empty, select Open audio files and pick both copies.
   - You should see: two items, each named after its file, with "Re-tag existing file". Nothing downloads.
2. Select the MP3.
   - You should see: "Re-tagging a file you already have", "Nothing is downloaded. Your changes are written into this file." and the file's folder. The fields show the cleaned-up title, the file's artist and album, and the file's cover with "From the file". The cleanups say "Cleaned up from the file's title". The button says "Save changes to the file".
3. Turn the title's cleanup off, so the title is as it was, and select Save changes to the file.
   - You should see: no question, then "Tags updated in" the file's name, with "Spotify can take a moment to notice" and a Spotify help button. The button opens Spotify help on "Songs not showing up".
4. Select the M4A, leave the cleaned-up title, and select Save changes to the file.
   - You should see: "Is this song in a Spotify playlist?" with Cancel, Save anyway and "Don't ask again". Cancel leaves it in review. Select Save changes to the file again, then Save anyway.
5. Open both files in the tag viewer.
   - You should see: the new tags, the same cover as before, and the file's link and comment unchanged. The files are where they were, with the same names.
6. Drop the MP3 again, mark it read-only in its Properties, and save it with a new year.
   - You should see: "The file is open in another program or is read-only." with Retry. The file is unchanged. Clear Read-only, then select Retry, and it saves.
7. Drop the M4A again, then move it to another folder before you save it.
   - You should see: "The file isn't there anymore." with Retry. Move it back and select Retry, and it saves.
8. Drop a text file renamed to `notes.mp3`.
   - You should see: "This file isn't MP3 or M4A audio that sc-mp3 can read." with Remove from the queue.
9. Drop the M4A again. Before you save it, change its title in another tag editor, then select Save changes to the file in sc-mp3.
   - You should see: "Another program changed this file since sc-mp3 read it, so it wasn't overwritten." The other editor's title is still in the file. Select Reload the file, and the editor shows what's in the file now.
10. Select the MP3 while its save is failing, as in step 6.
   - You should see: the fields, cleanups and cover can't be changed until it's saved or removed.
## 14. Spotify on the desktop

1. In Spotify, open Settings, turn on Show Local Files, choose Add a source and pick the library folder.
   - You should see: the saved tracks under Local Files in Your Library.
2. Check each saved track's title, artist, album and cover.
   - You should see: what sc-mp3 showed in its review, including the edits from section 3 and the cropped cover from section 4.
3. Play each track for a few seconds.
   - You should see: it plays, and its length matches SoundCloud's.

## 15. Spotify on phones

These need a person with the phones.

1. **iPhone.** In Spotify on the phone, turn on Settings and privacy › Apps and devices › Local audio files. Then copy the saved MP3 files, not the folders they're in, into Files › On My iPhone › Spotify, with file sharing in the Apple Devices app or iTunes.
   - You should see: the tracks under Local Files with the right title, artist, album and cover, and they play.
2. **Android.** Turn on the same setting, then copy the MP3 files to the phone's Music folder over USB.
   - You should see: the same as on the iPhone.
3. **Changes.** Edit one track's title in sc-mp3 and save it again with Replace. If Spotify on the desktop still shows the old title, try the three steps in Spotify help, "Songs not showing up".
   - You should see: the new title after one of the three steps.

## 16. Record the run

Write down the version, the date, the computer and OS, the Spotify versions, and each step that failed with what you saw instead. Attach the diagnostics from section 10 to each bug you report.
