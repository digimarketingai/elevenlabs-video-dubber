# 🎬 Video Dubber + Live Subtitle Editor
# 影片配音與即時字幕編輯器

English + Traditional Chinese documentation.  
英文與繁體中文說明。

Upload a video, create a dub, edit subtitles in Gradio, and export an MP4.

上傳影片、產生配音、在 Gradio 編輯字幕，再匯出 MP4。

> Community project. Not an official ElevenLabs application.
>
> 本工具為社群專案，並非 ElevenLabs 官方應用程式。

---

## English

### Features

- One-command Google Colab setup.
- Local Python execution.
- No app login.
- Each user supplies their own ElevenLabs API key for dubbing.
- Local video upload and optional YouTube import.
- Original, translated, bilingual, or no subtitles.
- Editable subtitle text, start time, and end time.
- Live subtitle preview after committing a table edit.
- Import UTF-8 SRT files.
- Export MP4, SRT, VTT, and subtitle-edit JSON.
- Fast selectable-subtitle export.
- Optional permanently burned-in subtitles.
- Animated processing indicator, elapsed timer, and stage log.
- Resume an existing dubbing project within the same browser session.
- Optional Traditional Chinese conversion for recognized Chinese text.

### Important: subtitles versus spoken audio

Editing subtitle text changes the captions only.

It does not regenerate, correct, or replace the spoken dubbing audio.
For example, changing a caption from “ten” to “twenty” does not make the
voice say “twenty.”

Automatic original captions are transcribed from the original audio.
Automatic translated captions are transcribed from the generated dub.

They are editable speech-recognition results, not a guaranteed exact copy
of ElevenLabs’ internal translation script.

This application does not use Enterprise transcript editing or audio
regeneration endpoints.

---

## Start in Google Colab

The GitHub repository must be public and contain:

```text
app.py
requirements.txt
colab.sh
README.md
.gitignore
```

Open a new Google Colab notebook and paste this into one code cell:

```python
!git clone https://github.com/digimarketingaii/elevenlabs-video-dubber.git /content/video-dubber && bash /content/video-dubber/colab.sh
```

Run the cell, wait for installation, and open the printed Gradio share link.

Keep the cell running while using the application.

### Restart in the same runtime

Stop the previous running cell, then run:

```python
!bash /content/video-dubber/colab.sh
```

Do not run the clone command again into the same existing folder.

### Update an existing Colab checkout

Stop the app first:

```python
!git -C /content/video-dubber pull --ff-only
!bash /content/video-dubber/colab.sh
```

If you have edited repository files locally, Git may require you to resolve
those changes before updating.

### Colab limitations

Colab is a temporary notebook runtime, not permanent web hosting.

Google warns that free-tier runtimes used primarily through a web UI for
content generation may be terminated. Use an appropriate paid runtime
or run locally if your use requires it.

Download your videos and subtitle files before the runtime ends.
Do not rely on Colab local storage for permanent backups.

The launcher installs FFmpeg and Noto CJK fonts using apt.
It is intended for Colab's Linux environment.

---

## Quick workflow

### A. Dub and subtitle a video

1. Upload a video or choose YouTube and enter a URL.
2. Enter your own ElevenLabs API key.
3. Select source and target languages.
4. Choose the start time and clip length.
5. Leave automatic captions enabled.
6. Confirm content/voice permission and API charges.
7. Click **Create new dub**.
8. Wait for dubbing and subtitle recognition.
9. Choose original, translated, or bilingual subtitles.
10. Click **Load / switch preview**.
11. Edit subtitle text and timestamps.
12. Press Enter or click outside a cell to commit each edit.
13. Choose an export method.
14. Click **Export edited video + subtitles**.
15. Download the files.

The default clip is 20 seconds. Test a short clip first.

### B. Original-language subtitles without dubbing

No ElevenLabs key is needed for this workflow.

1. Upload a video.
2. Leave automatic captions enabled.
3. Confirm permission.
4. Click **Prepare original only**.
5. Select **Original** audio.
6. Select **Original** subtitles.
7. Load the preview.
8. Edit and export.

This workflow generates original captions only.
It does not automatically translate text without creating a dub.

You can import your own translated SRT into the Translated tab.

---

## Live editing

Each subtitle table contains:

| Column | Meaning |
|---|---|
| Start | Start time in seconds |
| End | End time in seconds |
| Text | Caption text |

Times are relative to the prepared clip.

For example, if you cut a clip starting at 60 seconds in the original video,
subtitle time `0.0` means the beginning of that new clip—not 60 seconds.

Use the table controls to add or delete rows.
Blank text rows are ignored.

After committing a cell edit, the browser updates the subtitle track
without re-encoding the video or resetting playback.

“Live” means interactive editing of an uploaded clip, not live-stream
speech recognition or instant re-rendering of the MP4.

Invalid timing rows may be omitted from preview; export validates them.
Use:

```text
0 ≤ start < end
```

Captions beyond the end of the clip are omitted or clipped during export.

### Saving edits

Preview changes are not automatically written into the downloaded video.

Click **Export** to save the current tables and produce new output files.

Edits can be lost when the browser session or Colab runtime ends.
Download SRT and JSON files as backups.

The **Regenerate captions** button replaces the existing subtitle tables.
Export your edits first if you want to preserve them.

### Audio selection

You can preview and export either:

- Original audio.
- Dubbed audio, after dubbing has completed.

After changing the audio selection, click **Load / switch preview**.

Original and translated caption timing may differ slightly.
Review bilingual captions and adjust timings when necessary.

---

## Export options

### Soft subtitles — faster

Adds one selectable subtitle track containing your selected display mode.

The prepared video and audio streams are copied rather than re-encoded.

Subtitles require a player that supports MP4 subtitle tracks.
Some websites and social platforms may ignore or remove them.

Separate SRT and VTT files are also provided.

### Burn in — permanently visible

Renders subtitles directly into the video picture.

This requires video encoding and is slower than soft-subtitle export.

The font-size control applies to burned-in subtitles.
Browser preview styling is not an exact representation of the final burn-in.

The Colab launcher installs Noto CJK fonts for Chinese, Japanese, and Korean.
Other writing systems may require additional fonts.

### No subtitles

Select **None** as the subtitle display mode.
The app copies the selected prepared video without added captions.

### Downloaded files

Depending on the available subtitle tables, export provides:

```text
video.mp4
original.srt
translated.srt
selected.srt
selected.vtt
subtitle_edits.json
```

SRT imports must be UTF-8 and use the prepared clip's timeline.
Importing a full-video SRT does not automatically subtract the clip start time.

---

## Speed notes

The app prepares one smaller H.264 clip and sends its audio to ElevenLabs.

After dubbing, it copies the prepared video stream when assembling the dub.

Live subtitle edits happen in the browser.
They do not start FFmpeg or create a new paid dubbing request.

For faster output:

- Use a short local clip.
- Select 480p.
- Use soft subtitles.
- Load the video preview only when editing.
- Disable automatic captions if you already have SRT files.

The first automatic-caption run downloads a Whisper model.
Caption recognition uses CPU/int8 by default, even if a GPU is available.

The moving activity bar indicates ongoing work; it is not a completion
percentage. The log shows actual processing stages.

`+faststart` helps MP4 playback start earlier.
It does not increase your internet download speed.

YouTube import downloads the source before trimming.
Uploading an already trimmed local clip is usually simpler for testing.

---

## Costs and account access

Local subtitle recognition, editing, and export do not call ElevenLabs.

Creating a dub uses ElevenLabs and may incur charges according to your
account and the provider's current pricing.

A new project can incur a charge before the final audio is ready.
The app does not automatically retry project creation.

Use an API key with permission to create/read dubbing projects.
This app does not bypass account restrictions, pricing, watermark
requirements, or content permissions.

Only use videos and voices you have permission to process.

### Resume

Resume uses the project ID from the current browser session.
It does not submit a new project-creation request.

It requires the local clip and job files to still exist.

Resume does not regenerate failed or stale language targets.

If the initial creation request times out before an ID is returned,
the server may still have accepted it. Check your ElevenLabs account
before creating another project.

Closing the browser does not cancel an already submitted cloud job.

---

## Local installation

Requirements:

- Python 3.10 or later.
- FFmpeg and FFprobe on PATH.
- Internet access for dependencies, model download, and dubbing.
- Sufficient RAM and disk space.
- Suitable fonts for burned-in subtitles.

Clone:

```bash
git clone https://github.com/digimarketingaii/elevenlabs-video-dubber.git
cd elevenlabs-video-dubber
```

Create an environment:

```bash
python -m venv .venv
```

Activate on Windows:

```bat
.venv\Scripts\activate
```

Activate on macOS/Linux:

```bash
source .venv/bin/activate
```

Install and run:

```bash
python -m pip install -r requirements.txt
python app.py
```

Open:

```text
http://127.0.0.1:7860
```

Ubuntu/Debian dependencies:

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg fonts-noto-cjk python3-venv
```

macOS with Homebrew:

```bash
brew install ffmpeg
```

Install a suitable CJK font separately if needed.

For Windows, install an FFmpeg build with libass support and add its `bin`
folder to PATH.

Verify:

```bash
ffmpeg -version
ffprobe -version
ffmpeg -filters
```

Burn-in requires the `ass` filter.

### Public share link

```bash
python app.py --share
```

There is deliberately no application login.

---

## YouTube import

YouTube importing depends on yt-dlp, website behavior, network access,
and a supported JavaScript runtime.

The app detects Deno or Node when available.
The Colab launcher does not install a JavaScript runtime.

If needed, install a current supported runtime using the official
yt-dlp instructions.

Update yt-dlp:

```bash
python -m pip install -U "yt-dlp[default]"
```

If importing still fails, upload an authorized local video.
The app does not bypass DRM, sign-in restrictions, or access controls.

---

## Limits

Application limits:

- Uploaded source: 500 MB.
- YouTube source: non-live, up to 20 minutes.
- Prepared clip: 5–120 seconds requested.
- SRT import: 2 MB.
- Subtitle table: up to 2,000 nonempty rows.
- One heavy processing task at a time.
- Cloud polling: 30 minutes before returning a timeout.

These are application limits, not universal provider limits.

Automatic speech recognition and dubbing are imperfect.
Review text, translation, timing, names, and pronunciation.

The export preserves the prepared video's duration.
Longer dubbed audio is trimmed; shorter audio is padded with silence.
Lip synchronization is not guaranteed.

---

## Privacy and cleanup

No login also means this is not a hardened multi-user private service.

A public share link allows others to consume the host's CPU, disk, and
bandwidth. Do not assume generated files are isolated private storage.

Run locally for sensitive content.
Only enter API keys into instances you trust.

The host receives your API key to call ElevenLabs.
The app does not intentionally save API keys in job JSON.
Gradio run history is disabled.

Working files remain under:

```text
work/
```

This includes source clips, dubbed files, subtitles, and job metadata.

Stop the app and delete `work/` after downloading what you need.
Do not delete working files while processing.

Gradio's temporary-file cleanup is separate from the `work/` directory.

---

## 繁體中文

### 功能

- Google Colab 一行指令啟動。
- 支援本機 Python。
- 不需登入本工具。
- 配音時使用者輸入自己的 ElevenLabs API 金鑰。
- 支援影片上傳及選用的 YouTube 匯入。
- 可選原文、翻譯、雙語或不顯示字幕。
- 編輯字幕文字、開始時間及結束時間。
- 完成儲存格編輯後，即時更新預覽字幕。
- 匯入 UTF-8 SRT。
- 匯出 MP4、SRT、VTT 及字幕編輯 JSON。
- 快速匯出可關閉字幕。
- 選擇永久燒錄字幕。
- 動態處理圖示、計時器及處理紀錄。
- 相同瀏覽器工作階段可繼續查詢既有配音。
- 可將辨識出的中文轉為繁體中文。

### 字幕編輯與語音的差別

修改字幕只會改變顯示文字，不會重新生成配音。

例如把字幕「十」改成「二十」，語音仍然會說原本的內容。

原文字幕由原始音訊辨識產生。
翻譯字幕由已生成的配音音訊辨識產生。

這些是可編輯的語音辨識結果，不保證與 ElevenLabs 內部翻譯稿完全一致。

本工具不使用 Enterprise 專用的逐字稿編輯或語音重新生成 API。

---

## Google Colab 一行啟動

請先確認公開 GitHub 儲存庫已包含：

```text
app.py
requirements.txt
colab.sh
README.md
.gitignore
```

開啟新的 Google Colab 筆記本，貼上以下指令並執行：

```python
!git clone https://github.com/digimarketingaii/elevenlabs-video-dubber.git /content/video-dubber && bash /content/video-dubber/colab.sh
```

等待安裝完成，再開啟畫面中的 Gradio 分享連結。

使用期間請保持儲存格執行。

### 同一執行階段重新啟動

先停止原本執行中的儲存格，再執行：

```python
!bash /content/video-dubber/colab.sh
```

不要對已存在的相同資料夾重複執行 clone。

### 更新程式

先停止應用程式：

```python
!git -C /content/video-dubber pull --ff-only
!bash /content/video-dubber/colab.sh
```

如果您曾自行修改程式檔案，可能需要先處理 Git 的修改衝突。

### Colab 注意事項

Colab 是暫時性的筆記本執行環境，不是永久網站主機。

Google 提醒，免費執行環境若主要透過網頁介面進行內容生成，
可能被終止。必要時請使用合適的付費環境或改為本機執行。

請在執行階段結束前下載影片與字幕，不要將 Colab 本機儲存空間
當作永久備份。

啟動腳本使用 apt 安裝 FFmpeg 及 Noto CJK 字型，
適用於 Colab 的 Linux 環境。

---

## 操作流程

### 配音並加入字幕

1. 上傳影片，或選擇 YouTube 並輸入網址。
2. 輸入自己的 ElevenLabs API 金鑰。
3. 選擇原始語言及配音語言。
4. 設定開始秒數與片段長度。
5. 保持「自動產生字幕」開啟。
6. 確認內容、聲音授權及 API 費用。
7. 按「建立新配音」。
8. 等待配音與字幕辨識完成。
9. 選擇原文、翻譯或雙語字幕。
10. 按「載入／切換預覽」。
11. 編輯字幕文字及時間。
12. 按 Enter 或點選儲存格外完成編輯。
13. 選擇匯出方式。
14. 按「匯出影片與字幕」。
15. 下載檔案。

預設處理 20 秒，建議先用短片測試。

### 不配音，只加入原文字幕

此流程不需要 ElevenLabs API 金鑰。

1. 上傳影片。
2. 保持自動字幕開啟。
3. 確認授權。
4. 按「僅準備原音影片」。
5. 音訊選擇「原音」。
6. 字幕選擇「原文字幕」。
7. 載入預覽、編輯並匯出。

此流程不會自動翻譯文字。
如已有翻譯字幕，可在「翻譯」分頁匯入自己的 SRT。

---

## 即時字幕編輯

表格包含：

| 欄位 | 說明 |
|---|---|
| Start / 開始 | 字幕開始秒數 |
| End / 結束 | 字幕結束秒數 |
| Text / 文字 | 字幕內容 |

時間以裁切後的片段計算。

例如從原始影片第 60 秒開始裁切，
字幕的 `0.0` 表示新片段的起點，而不是原始影片第 60 秒。

可使用表格控制項新增或刪除字幕列。
空白文字列會被忽略。

完成儲存格編輯後，瀏覽器會更新字幕，不需要重新編碼影片，
也不會重新啟動影片播放。

此處「即時」是指已上傳片段的互動編輯，
不是直播語音辨識，也不是每次輸入文字就重新生成 MP4。

時間格式必須符合：

```text
0 ≤ 開始時間 < 結束時間
```

無效時間可能不會顯示於預覽，匯出時會進行驗證。
超過片段結尾的字幕會被略過或裁切。

### 儲存修改

預覽更新不代表下載影片已經改變。

請按「匯出」儲存目前表格，並產生新的影片與字幕檔案。

瀏覽器工作階段或 Colab 執行階段結束後，未匯出的修改可能遺失。
建議下載 SRT 及 JSON 作為備份。

「重新辨識字幕」會取代既有表格，請先匯出要保留的修改。

### 音訊選擇

可選擇原音，或已生成的配音。

變更音訊後，請再按一次「載入／切換預覽」。

原文與翻譯字幕的時間可能略有不同；
雙語字幕請自行檢查並調整時間。

---

## 匯出方式

### 可關閉字幕：較快

將所選的字幕顯示模式加入一條可選取的 MP4 字幕軌。

影像與音訊不重新編碼，因此匯出較快。

播放器需要支援 MP4 字幕軌。
部分網站或社群平台可能忽略或移除字幕軌。

程式也會提供獨立 SRT 與 VTT。

### 永久燒錄字幕：較慢

字幕直接寫入影片畫面。

此方式需要重新編碼影像，因此比可關閉字幕慢。

字級設定僅用於永久燒錄。
瀏覽器預覽與最終燒錄的字型排版不保證完全一致。

Colab 腳本會安裝 Noto CJK 字型。
其他文字系統可能需要額外字型。

### 無字幕

字幕顯示模式選擇「無字幕」，
即可複製所選的原音或配音影片，不加入字幕。

### 下載檔案

依現有字幕內容，可能提供：

```text
video.mp4
original.srt
translated.srt
selected.srt
selected.vtt
subtitle_edits.json
```

匯入的 SRT 必須是 UTF-8，且使用裁切後片段的時間軸。

匯入完整影片的 SRT 時，程式不會自動扣除裁切開始時間。

---

## 速度說明

程式會先準備較小的 H.264 片段，再將音訊傳送至 ElevenLabs。

合併配音時直接複製已處理的影像，不再次編碼影像。

字幕即時編輯在瀏覽器內更新，不會每次啟動 FFmpeg，
也不會重新建立付費配音工作。

如需更快：

- 使用短的本機片段。
- 選擇 480p。
- 使用可關閉字幕。
- 需要編輯時才載入預覽。
- 已有 SRT 時可關閉自動字幕。

首次辨識會下載 Whisper 模型。
預設使用 CPU/int8，即使執行環境有 GPU 也不會自動切換。

動態狀態列代表處理仍在進行，並非完成百分比。
處理紀錄會顯示實際階段。

`+faststart` 有助於較早開始播放，但不會提高網路下載速度。

---

## 費用與繼續功能

本機字幕辨識、編輯及匯出不會呼叫 ElevenLabs。

建立配音會使用 ElevenLabs，費用依帳戶與官方當時定價而定。
專案可能在最終音訊完成前就產生費用。

程式不會自動重送建立專案請求。

請使用具有配音專案建立及讀取權限的 API 金鑰。
本工具不會繞過帳戶限制、費用、浮水印要求或內容授權規定。

「繼續查詢配音」使用目前工作階段的專案 ID，
不會建立新的配音專案。

需要相同工作階段及仍存在的本機工作檔案。
此功能不會重新生成失敗或 stale 狀態的配音。

若建立請求逾時，且尚未取得專案 ID，
伺服器仍可能已接受請求。
請先查看 ElevenLabs 帳戶，再決定是否重新建立。

關閉瀏覽器不會取消已送出的雲端工作。

---

## 本機安裝

需求：

- Python 3.10 以上。
- FFmpeg 與 FFprobe 已加入 PATH。
- 可連線下載套件、模型及使用配音 API。
- 足夠的記憶體與磁碟空間。
- 燒錄字幕所需字型。

```bash
git clone https://github.com/digimarketingaii/elevenlabs-video-dubber.git
cd elevenlabs-video-dubber
python -m venv .venv
```

Windows 啟用環境：

```bat
.venv\Scripts\activate
```

macOS/Linux 啟用環境：

```bash
source .venv/bin/activate
```

安裝並執行：

```bash
python -m pip install -r requirements.txt
python app.py
```

開啟：

```text
http://127.0.0.1:7860
```

公開分享：

```bash
python app.py --share
```

本工具刻意不設登入功能。

---

## 限制與隱私

本工具限制：

- 上傳來源：500 MB。
- YouTube：非直播，20 分鐘以內。
- 片段長度：5 至 120 秒。
- SRT 匯入：2 MB。
- 字幕表格：最多 2,000 個非空白列。
- 同時執行一項主要處理工作。
- 雲端結果等待 30 分鐘後會回傳逾時。

這些不是 ElevenLabs 的通用限制。

語音辨識、翻譯、時間與發音皆可能有誤，請人工確認。

輸出以準備後的影片長度為準。
較長配音會裁切，較短配音會補上靜音，不保證嘴型同步。

沒有登入功能，也代表它不是已強化的多使用者私人服務。

公開連結的使用者可以消耗主機 CPU、磁碟及頻寬，
不應假設輸出檔案具有個別使用者的隱私隔離。

敏感內容請在本機處理。
僅在可信任的主機輸入 API 金鑰。

主機會接收金鑰以呼叫 ElevenLabs。
程式不會刻意將金鑰寫入工作 JSON，Gradio 執行歷史紀錄已關閉。

工作檔案儲存在：

```text
work/
```

下載所需檔案並停止程式後，可刪除此資料夾。
處理期間請勿刪除。

---

## Official documentation / 官方文件

- [ElevenLabs dubbing quickstart](https://elevenlabs.io/docs/eleven-api/guides/cookbooks/dubbing)
- [ElevenLabs create project](https://elevenlabs.io/docs/api-reference/dubbing/create-project)
- [Gradio](https://www.gradio.app/)
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- [yt-dlp and JavaScript runtime instructions](https://github.com/yt-dlp/yt-dlp)
- [FFmpeg](https://ffmpeg.org/)
- [Google Colab FAQ](https://research.google.com/colaboratory/faq.html)
- [OpenCC Python](https://github.com/yichen0831/opencc-python)
