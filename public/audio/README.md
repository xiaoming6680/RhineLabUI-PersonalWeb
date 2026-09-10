# 音频来源与处理记录

## 开场录音与背景音乐（参考网站录屏）

`boot-intro.ogg` 与 `bgm-loop.ogg` 来自用户提供的参考网站录屏 `音视频参考.mp4`（约 239 秒，44.1 kHz 立体声 AAC）。精确切点、源文件校验、分离模型与循环参数见 `bgm-source.json`；运行时使用的循环区间在 `src/bgm-loop.ts`。

- `boot-intro.ogg`：录屏 0.50 秒（第一帧白场，对应参考时间 6.76 秒）到音乐起点之间的声音，1:1 截取，不做任何处理。开场期间按参考时钟播放，跳转、倒退或冻结即停止并从新位置继续。
- `bgm-loop.ogg`：录屏音乐起点之后的配乐，用 audio-separator 的 BS-Roformer 模型去除人声，再按节拍网格挑选循环区间，并把区间末尾与区间开头前的等功率淡变烘焙进文件。首次从头播放，之后在 `loopStart`–`loopEnd` 之间循环。

生成命令（需要 Python 3、`imageio-ffmpeg`、`audio-separator`、`librosa`、`soundfile`）：

```powershell
python scripts/build-bgm.py 'D:/!XM的项目/06_个人项目/office-building-showcase/音视频参考.mp4'
```

`--work` 指定中间文件目录（默认 `.tools/bgm/`，含分离后的无损人声/伴奏、`seam-preview.wav` 接缝试听）；`--choose n` 选择排名第 n 的候选循环，`--start/--end` 强制指定循环点。

这两段录音及其衍生文件的权利归原作者，不纳入本仓库的 MIT 授权声明。

## 逐字输入短音

`typing-preview.wav` 及 `src/typing-samples.ts` 使用用户提供的《明日方舟》特别映像 [莱茵生命：访问] 中约 6.864–6.986 秒的三个短音，各 38ms。`typing-source.json` 保留精确时间、源文件校验与处理参数。原音及其衍生片段的权利归原作者。

提取脚本：`scripts/extract-typing-audio.mjs`。原片短音仅去直流、做边缘淡变和统一增益，没有变调、变速或合成替换。`reference/typing-original.wav` 为本地对照片段，不属于生产配乐。开场现在直接播放录屏原声，这些短音仅供 `key` 音效与试听页使用。

## 音效

交互音效由 `src/audio.ts` 程序合成，随项目采用仓库 LICENSE。
