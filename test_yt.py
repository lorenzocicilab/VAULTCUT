import yt_dlp

ydl_opts = {'quiet': True, 'no_warnings': True}

with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    info = ydl.extract_info('https://www.youtube.com/watch?v=dQw4w9WgXcQ', download=False)
    print(f'Title: {info["title"]}')
    print(f'Duration: {info["duration"]} seconds')
    print(f'Views: {info["view_count"]}')
