"""选题包：把多条素材打成一份「给 agent 消费的选题材料」，并自动找出口径分歧候选。

为什么要这个：素材库 647 条攻略里只有 29 条进过整理稿，产线以「一条视频」为单位出稿，
而读者和平台以「一个问题」为单位消费。同一个机制被几个 up 主同时讲、说法互相矛盾——
这正是唯一抄不到的独家增量，但单条整合稿看不见它（一次只喂 1-2 条素材）。

选题包不做生成，只做「捞全 + 对齐」：清单、口播原文、同题素材簇、各家数字并排。
生成由 Qoder 会话按 content-research-writer + 头条出稿规范 v1.1 完成。

聚类口径（不硬编领域词表，词表会跟着游戏漂移）：
  1. 每条素材取 2-gram 集合（标题 + ai_tags 滑窗）；
  2. 出现在多数素材里的 gram 是「魔兽/无限」这类通用词，按整批占比自动剔掉；
  3. 剩下每个「话题词」各自把 48 小时内的素材聚一组（≥2 条且 ≥2 个作者才算一簇）；
  4. 成员重叠 60% 以上的组合并——同一件事会被好几个词各聚一次。
  不用并查集传递合并：实测 60 条同游戏素材会被串成一大坨，等于没分。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Sequence, Tuple

# 转写截断：单条给足决策上下文即可，整包再设总预算，避免一份包把会话上下文吃满
TRANSCRIPT_CAP = 2600
PACK_CHAR_BUDGET = 60000
STOP_RATIO = 0.55          # 出现在 55% 以上素材里的 gram 视为通用词
WINDOW_HOURS = 48          # 算「同一件事」的时间窗
MERGE_RATIO = 0.8          # 成员重叠这么多才合并；0.6 实测会把「RMT处罚」和「达拉然新副本」并成一坨
NUM_PHRASE = re.compile(r'\d+(?:\.\d+)?\s*(?:小时|级|天|周|%|％|个|元|人|分钟|次|层|件)')


def _grams(text: str) -> set:
    raw = re.sub(r'[\s，。、！？：；「」『』（）()\[\]/\-—#*.]+', '', text or '')
    return set(raw[i:i + 2] for i in range(len(raw) - 1))


def _tag_tokens(tags: str) -> set:
    """ai_tags 是模型打好的空格分词，直接拿词根当话题词。

    为什么不用标题的滑窗 2-gram：跨词切会造出「兽无」「限服」「Be/ta」这类碎片，
    实测 60 条素材聚出 50 个簇、全是噪声。标签取首尾两字，能对上
    「经验调整 / 经验削减 / 副本经验」这种同词根不同后缀的情况。
    """
    out = set()
    for tag in re.split(r'[\s,，、;；/|]+', tags or ''):
        tag = tag.strip()
        if not tag:
            continue
        if re.search(r'[A-Za-z]', tag):
            out.add(tag.lower())
        if len(tag) <= 2:
            out.add(tag)
        else:
            out.add(tag[:2])
            out.add(tag[-2:])
    return out


def _parse_time(value: str) -> datetime:
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime((value or '').strip(), fmt)
        except ValueError:
            continue
    return datetime.min


def _freq_of(videos: Sequence[Dict[str, Any]]) -> Tuple[Dict[int, set], Dict[str, int]]:
    """每条素材的话题词原样返回 + 词频。剔通用词的判断只在 find_clusters 里做一次，
    两处各算一套阈值曾把「经验」这类核心词提前删掉，导致该聚的聚不起来。"""
    per: Dict[int, set] = {}
    for v in videos:
        tokens = _tag_tokens(v.get('ai_tags') or '')
        if not tokens:            # 没打过标的老素材，退回标题滑窗
            tokens = _grams(v.get('title') or '')
        per[v['id']] = tokens
    freq: Dict[str, int] = {}
    for grams in per.values():
        for g in grams:
            freq[g] = freq.get(g, 0) + 1
    return per, freq


def _within_hours(a: Dict[str, Any], b: Dict[str, Any], seconds: float) -> bool:
    ta, tb = _parse_time(a.get('published_at')), _parse_time(b.get('published_at'))
    if ta == datetime.min or tb == datetime.min:
        return True   # 没有时间信息就别拿它当拆分依据
    return abs((tb - ta).total_seconds()) <= seconds


def _number_lines(members: Sequence[Dict[str, Any]]) -> List[str]:
    """把每条素材里「带单位的数字短语」并排列出来——满级 160 还是 200 小时，一眼能比。"""
    lines = []
    for m in members:
        text = (m.get('transcript') or '')[:TRANSCRIPT_CAP]
        hits: List[str] = []
        for phrase in NUM_PHRASE.findall(text):
            if phrase not in hits:
                hits.append(phrase)
        if hits:
            lines.append('%s：%s' % ((m.get('author') or '?'), '、'.join(hits[:8])))
    return lines


def find_clusters(videos: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """返回「同一件事被多个 up 主讲」的素材簇，按涉及条数倒序。"""
    if len(videos) < 2:
        return []
    per, freq = _freq_of(videos)
    ordered = sorted(videos, key=lambda v: _parse_time(v.get('published_at')))
    floor = max(3, int(len(ordered) * STOP_RATIO))
    # 出现 2 次到 floor 次的词才是"话题词"：只出现 1 次没人能对得上，
    # 出现太多（魔兽/无限/无限服）等于每条都有，聚不出东西
    allowed = {g for g, n in freq.items() if 2 <= n <= floor}
    terms = {vid: grams & allowed for vid, grams in per.items()}
    groups: List[Dict[str, Any]] = []

    for g in sorted(allowed):
        hits = [v for v in ordered if g in terms[v['id']]]
        i = 0
        while i < len(hits):
            j = i
            while j + 1 < len(hits) and _within_hours(hits[i], hits[j + 1], WINDOW_HOURS * 3600):
                j += 1
            chunk = hits[i:j + 1]
            authors = {(v.get('author') or '').strip() for v in chunk} - {''}
            if len(chunk) >= 2 and len(authors) >= 2:
                groups.append({'members': list(chunk), 'keys': [g]})
            i = j + 1

    out = []
    for c in _merge_overlapping(groups):
        members = c['members']
        keys = sorted(set(c['keys']))
        # 只靠一个话题词聚起来的 2 条素材多半是巧合（同一天各讲一件事），
        # 要么 3 条以上、要么两个以上话题词互相印证，才算「同一件事」
        if len(members) < 3 and len(keys) < 2:
            continue
        out.append({
            'ids': [m['id'] for m in members],
            'authors': sorted({(m.get('author') or '').strip() for m in members} - {''}),
            'keys': keys[:8],
            'span': '%s ~ %s' % ((members[0].get('published_at') or '')[:10],
                                 (members[-1].get('published_at') or '')[:10]),
            'numbers': _number_lines(members),
        })
    out.sort(key=lambda c: (-len(c['ids']), c['span']))
    return out


def _merge_overlapping(groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """同一件事会被好几个话题词各聚一次，成员重叠高的合并掉。"""
    done: List[Dict[str, Any]] = []
    for g in sorted(groups, key=lambda x: -len(x['members'])):
        ids = {m['id'] for m in g['members']}
        if not ids:
            continue
        for kept in done:
            kid = {m['id'] for m in kept['members']}
            smaller = min(len(ids), len(kid))
            if smaller and len(ids & kid) / smaller >= MERGE_RATIO:
                kept['members'] = sorted(kept['members'] + [m for m in g['members']
                                                            if m['id'] not in kid],
                                         key=lambda v: v.get('published_at') or '')
                kept['keys'] = list(set(kept['keys']) | set(g['keys']))
                break
        else:
            done.append({'members': list(g['members']), 'keys': list(g['keys'])})
    return done


def build_pack(videos: Sequence[Dict[str, Any]], theme: str = '',
               used_ids: Iterable[int] = ()) -> Tuple[str, Dict[str, Any]]:
    """拼选题包 markdown，同时返回统计（前端显示用，也便于断言）。"""
    used = set(used_ids or ())
    fresh = [v for v in videos if v.get('id') not in used]
    clusters = find_clusters(videos)
    authors = sorted({(v.get('author') or '').strip() for v in videos if v.get('author')})
    times = [t for t in (_parse_time(v.get('published_at')) for v in videos) if t != datetime.min]
    span = '%s ~ %s' % (min(times).date(), max(times).date()) if times else '时间未知'
    by_time_desc = sorted(videos, key=lambda v: v.get('published_at') or '', reverse=True)

    lines = ['# 选题包 · %s' % (theme or '未命名主题'),
             '',
             '> 生成：%s ｜ 素材 %d 条 ｜ 覆盖 up 主 %d 个 ｜ 时间跨度 %s' % (
                 datetime.now().strftime('%Y-%m-%d %H:%M'), len(videos), len(authors), span),
             '> 其中 **%d 条从没进过整理稿**（%.0f%%）——这个包就是把这批沉睡素材一次用掉。' % (
                 len(fresh), 100.0 * len(fresh) / max(len(videos), 1)),
             '> 用法：在 Qoder 会话里说「按这个包出头条文章」，走 content-research-writer + /article-audit。',
             '',
             '## 一、素材清单',
             '',
             '| # | 发布 | up 主 | 标题 | 转写字数 | 出过整理稿 |',
             '|---|---|---|---|---|---|']
    for i, v in enumerate(by_time_desc, 1):
        lines.append('| %d | %s | %s | %s | %d | %s |' % (
            i, (v.get('published_at') or '')[:16], (v.get('author') or '')[:16],
            (v.get('title') or '')[:44], len(v.get('transcript') or ''),
            '—' if v.get('id') in used else '★ 没用过'))

    lines += ['', '## 二、口径分歧候选（同一件事，多个 up 主同时讲）', '']
    if not clusters:
        lines.append('没发现 48 小时内多来源覆盖同一话题的素材簇。这个包适合写单源攻略，'
                     '不适合写交叉核对稿——想写核对稿就再捞几条同题素材。')
    for n, c in enumerate(clusters[:6], 1):
        lines.append('### 分歧 %d ｜ %d 条 ｜ %s ｜ %s' % (
            n, len(c['ids']), c['span'], '、'.join(c['authors'][:6])))
        lines.append('- 聚起来的话题词：%s' % '、'.join(c['keys']))
        for v in sorted([x for x in videos if x['id'] in c['ids']],
                        key=lambda x: x.get('published_at') or ''):
            lines.append('  - %s %s：**%s**' % ((v.get('published_at') or '')[5:16],
                                                (v.get('author') or '')[:14],
                                                (v.get('title') or '')[:40]))
        if c['numbers']:
            lines.append('- 各家给的数字（对不上就是选题点；发布前必须回原片或官方口径核实）：')
            for row in c['numbers']:
                lines.append('  - %s' % row)
        lines.append('')

    lines += ['', '## 三、口播原文（按时间倒序，单条截断 %d 字）' % TRANSCRIPT_CAP, '']
    budget, cut = PACK_CHAR_BUDGET, 0
    for i, v in enumerate(by_time_desc, 1):
        head = '### %d) %s ｜ %s ｜ %s' % (i, (v.get('published_at') or '')[:16],
                                           v.get('author') or '', v.get('title') or '')
        text = (v.get('transcript') or '').strip()
        if not text:
            lines.append(head + '\n\n（没有 ASR 转写，只有标题——要写得回原片补）\n')
            continue
        if len(text) > TRANSCRIPT_CAP:
            text = text[:TRANSCRIPT_CAP] + '…（截断）'
            cut += 1
        if budget - len(text) < 0:
            lines.append('\n（整包字数预算用完，后面 %d 条转写未展开；需要时按更小主题再导一次）'
                         % (len(by_time_desc) - i + 1))
            break
        budget -= len(text)
        lines.append(head + '\n\n' + text + '\n')

    lines += ['', '---', '', '```',
              '按这份选题包出头条文章：走 content-research-writer 流程（先大纲 → 我补 300 字 → 成稿），'
              '规则按 ~/Documents/自媒体/头条出稿规范.md v1.1，写完跑 /article-audit 改到「可以发」。', '```', '']

    stats = {'videos': len(videos), 'unused': len(fresh), 'authors': len(authors),
             'clusters': len(clusters), 'chars': sum(len(l) + 1 for l in lines), 'truncated': cut}
    return '\n'.join(lines), stats
