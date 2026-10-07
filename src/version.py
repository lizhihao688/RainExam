"""
RainExam 版本号（唯一来源）

- 发布时由 GitHub Actions 按 tag 自动覆盖本文件（见 .github/workflows/build-release.yml）
  例如推送 tag v2.0.2 → 打包出的 exe 内部版本号为 2.0.2
- 本地开发/手动打包时使用下面这个默认值（应与最近一次发布保持一致，
  避免开发版一直提示有新版本）
"""

__version__ = "2.0.1"
