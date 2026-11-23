# Rounds

One YAML file per round. The file name is the round id: it shows in URLs
(`/?r=centrale-supelec`) and keys the answers in the database, so never
rename a file once people have played it.

```yaml
title: La fusion CentraleSupélec   # shown above the chart
date: 2026-11-23                    # hidden before that day; newest round opens first
axes:
  x: { neg: Centrale, pos: Supélec }      # left, right
  y: { neg: Absorption, pos: Fusion }     # bottom, top
candidates:                         # 2 to 40
  - { id: rer-b, name: Le RER B, emoji: "🚆" }
```

A candidate `id` is lowercase letters, digits and hyphens. Like the file
name, don't change it after the round opens: answers point to it. The name
and the emoji can change at any time.
