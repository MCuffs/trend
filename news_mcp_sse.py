"""
뉴스 MCP 서버 (SSE 버전) v2 - 이슈 기반 설계
Google News RSS 기반, API 키 불필요.

핵심 철학:
카테고리로 분류하지 않는다. 지금 터지고 있는 이슈를 잡아서,
그 이슈가 어떤 상품 기회로 연결되는지 Agent(LLM)가 해석한다.

예: '러브버그 출몰 심각' → Agent가 판단 → 살충제/방충제 캠페인
"""

import os
import json
import httpx
import feedparser
from urllib.parse import quote
from mcp.server.fastmcp import FastMCP

PORT = int(os.getenv("PORT", 8000))
mcp = FastMCP(
    "news-issue-detector",
    host="0.0.0.0",
    port=PORT,
)

# 트렌드 탐색용 시드 키워드 (광범위한 라이프스타일 이슈 커버)
TRENDING_SEEDS = [
    "이슈",
    "화제",
    "트렌드",
    "급증",
    "출몰",
    "유행",
    "인기",
    "대란",
]


async def fetch_google_news_rss(query: str, max_items: int = 10) -> list:
    """Google News RSS로 뉴스 검색"""
    encoded_query = quote(query)
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ko&gl=KR&ceid=KR:ko"

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        res = await client.get(url)
        res.raise_for_status()
        feed = feedparser.parse(res.text)

    items = []
    for entry in feed.entries[:max_items]:
        items.append({
            "title": entry.get("title", ""),
            "link": entry.get("link", ""),
            "published": entry.get("published", ""),
            "source": entry.get("source", {}).get("title", "") if hasattr(entry, "source") else "",
            "summary": entry.get("summary", "")[:200],
        })
    return items


@mcp.tool()
async def search_news(query: str, max_results: int = 10) -> str:
    """
    특정 키워드/이슈로 최신 뉴스를 검색합니다.
    이슈가 얼마나 뜨거운지, 어떤 맥락인지 파악할 때 사용합니다.

    Args:
        query: 검색 키워드 (예: '러브버그', '폭우', '마라톤 대회')
        max_results: 가져올 뉴스 개수 (기본 10, 최대 20)
    """
    max_results = min(max_results, 20)

    try:
        items = await fetch_google_news_rss(query, max_results)
    except Exception as e:
        return json.dumps({"error": f"뉴스 조회 실패: {e}"}, ensure_ascii=False)

    # 이슈 강도 평가
    count = len(items)
    if count >= 10:
        strength = "VERY_HIGH"
        interpretation = "매우 강한 이슈 - 대중 관심도 정점. 즉시 행동 필요."
    elif count >= 5:
        strength = "HIGH"
        interpretation = "강한 이슈 - 캠페인 기회. 48시간 내 집행 권장."
    elif count >= 2:
        strength = "MEDIUM"
        interpretation = "중간 이슈 - 지속 모니터링 + 얼리 무버 포지셔닝 가능."
    else:
        strength = "LOW"
        interpretation = "약한 이슈 또는 쿼리가 너무 구체적."

    return json.dumps({
        "query": query,
        "news_count": count,
        "issue_strength": strength,
        "interpretation": interpretation,
        "news": items,
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_trending_issues(max_issues: int = 15) -> str:
    """
    지금 한국에서 뜨고 있는 트렌드 이슈들을 광범위하게 수집합니다.
    카테고리 구분 없음. Agent가 각 이슈를 해석해서 상품 기회로 연결하도록 설계됨.

    사용 예시:
    - "요즘 뭐가 뜨고 있어?" → 이 도구 호출 → Agent가 각 이슈 해석
    - "캠페인 아이디어 뽑아줘" → 이 도구 + 해석 로직

    Args:
        max_issues: 수집할 이슈 개수 (기본 15, 최대 30)
    """
    max_issues = min(max_issues, 30)

    all_items = []
    seen_titles = set()

    # 여러 시드 키워드로 뉴스 수집
    for seed in TRENDING_SEEDS:
        try:
            items = await fetch_google_news_rss(seed, max_items=5)
            for item in items:
                # 제목 기반 중복 제거
                title_key = item["title"][:50]
                if title_key not in seen_titles:
                    seen_titles.add(title_key)
                    all_items.append(item)
        except Exception:
            continue

        if len(all_items) >= max_issues:
            break

    return json.dumps({
        "collected_issues": len(all_items),
        "instruction_for_agent": (
            "각 뉴스 제목을 읽고, 그 이슈가 어떤 소비자 수요를 만들어낼지 해석하세요. "
            "예: '러브버그 출몰' → 살충제/방충제 수요, "
            "'폭염 기록' → 쿨링 제품 수요, "
            "'배달비 인상' → 밀키트/간편식 수요. "
            "해석 후 관련 카테고리 관심 유저를 타겟팅한 캠페인을 제안하세요."
        ),
        "issues": all_items[:max_issues],
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_campaign_opportunities(issue_keyword: str) -> str:
    """
    특정 이슈에 대한 뉴스를 분석하여 캠페인 기회를 Agent가 추론할 수 있도록
    구조화된 데이터를 제공합니다.

    주의: 이 도구는 뉴스 데이터와 해석 가이드를 제공할 뿐,
    실제 상품 카테고리 매핑은 Agent(LLM)가 판단합니다.

    Args:
        issue_keyword: 분석할 이슈 키워드 (예: '러브버그', '폭우', '미세먼지')
    """
    try:
        items = await fetch_google_news_rss(issue_keyword, max_items=10)
    except Exception as e:
        return json.dumps({"error": f"뉴스 조회 실패: {e}"}, ensure_ascii=False)

    if not items:
        return json.dumps({
            "status": "no_issue",
            "message": f"'{issue_keyword}' 관련 뉴스 없음. 이슈 미형성."
        }, ensure_ascii=False)

    # 헤드라인들을 Agent가 해석하기 쉽게 정리
    headlines = [item["title"] for item in items]

    return json.dumps({
        "issue_keyword": issue_keyword,
        "evidence_news_count": len(items),
        "headlines_for_interpretation": headlines,
        "news_details": items,
        "agent_task": (
            f"'{issue_keyword}' 이슈를 분석해서 다음을 추론하세요:\n"
            "1. 이 이슈가 소비자에게 어떤 문제/욕구를 만드는가?\n"
            "2. 어떤 상품 카테고리가 이 욕구를 해결할 수 있는가?\n"
            "3. 어떤 유저 세그먼트를 타겟해야 하는가?\n"
            "   (예: 최근 관련 카테고리 조회 유저, 특정 지역 유저 등)\n"
            "4. 알림톡 메시지를 작성하라 (긴급성 + 공감 + CTA 포함)\n"
            "5. 캠페인 타이밍을 제안하라 (이슈 수명 고려)"
        ),
    }, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run(transport="sse")
