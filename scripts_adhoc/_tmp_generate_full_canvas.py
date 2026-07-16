"""Generate full-market canvas TSX from analysis JSON."""
from __future__ import annotations

import json
from pathlib import Path

ROWS = json.loads(Path(__file__).with_name("_tmp_canvas_rows.json").read_text())
META = json.loads(Path(__file__).with_name("_tmp_full_market_rotation_analysis.json").read_text())["meta"]
OUT = Path(
    r"C:/Users/Darius/.cursor/projects/d-FinanceProjects-edgarDataManagementPython"
    "/canvases/capital-rotation-cycle-wk28-29.canvas.tsx"
)

qc = ROWS["quadrant_counts"]
sector_chart = ROWS["sector_chart"]


def ts_rows(name: str, rows: list, row_type: str) -> str:
    body = ts_array(rows)
    return f"const {name}: {row_type} = {body};"


def ts_array(rows: list[list]) -> str:
    lines = ["["]
    for row in rows:
        cells = ", ".join(json.dumps(c) for c in row)
        lines.append(f"  [{cells}],")
    lines.append("]")
    return "\n".join(lines)


series_js = ",\n".join(
    "    { name: " + json.dumps(s["name"]) + ", data: " + json.dumps(s["data"])
    + (f', tone: "{s["tone"]}" as const' if "tone" in s else "")
    + " }"
    for s in sector_chart["series"]
)

header = '''import {
  H1,
  H2,
  H3,
  Text,
  Stack,
  Grid,
  Stat,
  Table,
  Callout,
  Divider,
  BarChart,
  Card,
  CardHeader,
  CardBody,
  CollapsibleSection,
  Pill,
} from "cursor/canvas";

'''

constants = "\n".join([
    f"const sectorMomentumCategories = {json.dumps(sector_chart['categories'])};",
    ts_rows("sectorRows", ROWS["sector_rows"], "Array<[string, string, string, string, string, string, string]>"),
    ts_rows("priceLeaders1w", ROWS["price_leaders_1w"], "Array<[string, string, string, string, string, string]>"),
    ts_rows("priceLaggards1w", ROWS["price_laggards_1w"], "Array<[string, string, string, string, string, string]>"),
    ts_rows("priceLeaders1m", ROWS["price_leaders_1m"], "Array<[string, string, string, string, string, string]>"),
    ts_rows("edgeImprovers", ROWS["edge_improvers"], "Array<[string, string, string, string, string]>"),
    ts_rows("edgeDecliners", ROWS["edge_decliners"], "Array<[string, string, string, string, string]>"),
    ts_rows("rotationRows", ROWS["rotation_rows"], "Array<[string, string, string, string, string, string]>"),
    ts_rows("laneRows", ROWS["lane_rows"], "Array<[string, string, string, string, string]>"),
    ts_rows("opportunityRows", ROWS["opportunity_rows"], "Array<[string, string, string, string, string, string, string, string, string]>"),
    ts_rows("earlyRotationRows", ROWS["early_rotation_rows"], "Array<[string, string, string, string, string, string, string, string]>"),
    ts_rows("momentumRows", ROWS["momentum_rows"], "Array<[string, string, string, string, string, string, string, string]>"),
    ts_rows("avoidRows", ROWS["avoid_rows"], "Array<[string, string, string, string, string, string, string, string]>"),
    ts_rows("setupRows", ROWS["setup_rows"], "Array<[string, string, string, string, string]>"),
])

body = f'''
export default function CapitalRotationFullMarket() {{
  return (
    <Stack gap={{24}}>
      <Stack gap={{8}}>
        <H1>Full-market capital rotation — all industries (≥$500M)</H1>
        <Text>
          {META["industry_count"]} industries merged across {META["run_count"]} edge parent runs
          ({META["earliest_run"]}–{META["latest_run"]}), all_fields {str(META["all_fields_date"])[:10]},
          latest suite ce6f36f2. Price momentum, edge lane drift, blindspot rotation, and name-level setups.
        </Text>
      </Stack>

      <Callout tone="info">
        Market structure: {qc["Early rotation (price lag)"]} industries show price lagging improving edge (best
        forward expression) · {qc["Momentum + edge"]} have both price and edge rising · {qc["Late / fading edge"]}
        are extended with fading setup quality · {qc["Avoid / unwind"]} have weak price and weak edge. Core thesis
        intact: semis/hardware correcting; software + health/nursing rotating in via blindspot and unified shortlist.
      </Callout>

      <Grid columns={{4}} gap={{12}}>
        <Stat label="Industries tracked" value="{META["industry_count"]}" />
        <Stat label="Early rotation (price lag)" value="{qc["Early rotation (price lag)"]}" tone="success" />
        <Stat label="Momentum + edge" value="{qc["Momentum + edge"]}" tone="info" />
        <Stat label="Avoid / unwind" value="{qc["Avoid / unwind"]}" tone="danger" />
      </Grid>

      <Card>
        <CardHeader trailing={{<Pill tone="neutral">sector rollup</Pill>}}>Sector momentum vs rotation</CardHeader>
        <CardBody>
          <BarChart
            categories={{sectorMomentumCategories}}
            series={{[
{series_js}
            ]}}
            height={{240}}
            beginAtZero={{false}}
          />
          <Text tone="secondary" size="small">
            Bars: median 1M price % by sector. Rotation score scaled ×10 for visual comparison. Source: all_fields +
            blindspot rotation · 13 Jul 2026.
          </Text>
        </CardBody>
      </Card>

      <Table
        headers={{["Sector", "Industries", "Names", "Med 1W", "Med 1M", "Med rotation", "Shortlist names"]}}
        rows={{sectorRows}}
        striped
      />

      <Divider />

      <H2>Movement quadrants — how to read every industry</H2>
      <Grid columns={{2}} gap={{16}}>
        <Callout tone="success" title="Early rotation (price lag) — highest priority">
          Price still soft or flat but edge med5 improving (&gt;+0.05pp since Jun 30). Capital has not fully repriced.
          Examples: Packaged Software, Advertising/Marketing, Medical Specialties. Lean in before breadth catches up.
        </Callout>
        <Callout tone="info" title="Momentum + edge — ride but size normally">
          Both 1W price and edge improving. Confirmed trends; watch for overcrowding as shortlist fills.
        </Callout>
        <Callout tone="warning" title="Late / fading edge — take profits / be selective">
          Price positive but edge med5 flat or falling. Includes many insurance and energy names running on price
          momentum while setup quality stalls.
        </Callout>
        <Callout tone="danger" title="Avoid / unwind — reduce exposure">
          Negative/neutral price and no edge improvement. Broad semi supply chain, electrical products, much of
          producer manufacturing.
        </Callout>
      </Grid>

      <H3>Early rotation industries (edge ahead of price)</H3>
      <Table
        headers={{["Industry", "Sector", "Med 1W", "Edge Δ med5", "Rotation", "Lane med5", "Shortlist", "Top ticker"]}}
        rows={{earlyRotationRows}}
        striped
      />

      <H3>Momentum + edge (confirmed)</H3>
      <Table
        headers={{["Industry", "Sector", "Med 1W", "Edge Δ med5", "Rotation", "Lane med5", "Shortlist", "Top ticker"]}}
        rows={{momentumRows}}
        striped
      />

      <Divider />

      <H2>Price movers — all industries (1W and 1M)</H2>
      <Grid columns={{2}} gap={{16}}>
        <Stack gap={{8}}>
          <H3>Top 1-week performers</H3>
          <Table headers={{["Industry", "Sector", "N", "Med 1W", "Med 1M", "% up 1W"]}} rows={{priceLeaders1w}} striped />
        </Stack>
        <Stack gap={{8}}>
          <H3>Bottom 1-week performers</H3>
          <Table headers={{["Industry", "Sector", "N", "Med 1W", "Med 1M", "% up 1W"]}} rows={{priceLaggards1w}} striped />
        </Stack>
      </Grid>

      <H3>Top 1-month performers</H3>
      <Table headers={{["Industry", "Sector", "N", "Med 1M", "Med 1W", "% up 1M"]}} rows={{priceLeaders1m}} striped />

      <Divider />

      <H2>Edge lane drift — all industries (Jun 30 → Jul 13)</H2>
      <Grid columns={{2}} gap={{16}}>
        <Stack gap={{8}}>
          <H3>Edge med5 improvers</H3>
          <Table headers={{["Industry", "Δ med5 pp", "Latest med5", "Occurrences", "Win rate 5d"]}} rows={{edgeImprovers}} striped />
        </Stack>
        <Stack gap={{8}}>
          <H3>Edge med5 decliners</H3>
          <Table headers={{["Industry", "Δ med5 pp", "Latest med5", "Occurrences", "Win rate 5d"]}} rows={{edgeDecliners}} striped />
        </Stack>
      </Grid>

      <Divider />

      <H2>Blindspot rotation — all ranked industries</H2>
      <Text tone="secondary" size="small">
        Quiet capital flow: breadth delta + relative-strength acceleration on names not yet quant-hot. Full top-28
        from ce6f36f2 blindspot lane (price source 10 Jul).
      </Text>
      <Table
        headers={{["Rank", "Industry", "Sector", "Rotation score", "Med 1M perf", "Top quiet movers"]}}
        rows={{rotationRows}}
        striped
      />

      <Divider />

      <H2>Historical lane leaders — edge in-setup returns by industry</H2>
      <Text tone="secondary" size="small">
        adrp_relvol_core lane medians from edge_lane_leaders. Use to validate whether a price-moving industry also has
        repeatable tactical edge.
      </Text>
      <Table
        headers={{["Industry", "Occurrences", "Med 5d fwd", "Win 5d", "Med 20d fwd"]}}
        rows={{laneRows}}
        striped
      />

      <Divider />

      <H2>Composite opportunity rank — top industries</H2>
      <Text tone="secondary" size="small">
        move_score blends 1W/1M price, edge med5 delta, blindspot rotation, and lane med5. Prioritisation map across
        all 129 industries.
      </Text>
      <Table
        headers={{["Industry", "Sector", "Quadrant", "Med 1W", "Med 1M", "Edge Δ", "Rotation", "Shortlist", "Top ticker"]}}
        rows={{opportunityRows}}
        striped
      />

      <Divider />

      <H2>Name-level setups by industry (lane-specific)</H2>
      <Text tone="secondary" size="small">
        Top unified + blindspot + screen names per industry. 103 industries with actionable names; top 40 by move_score.
      </Text>
      <Table
        headers={{["Industry", "Quadrant", "Med 1W", "Rotation", "Setups (ticker: source, key metrics)"]}}
        rows={{setupRows}}
        striped
      />

      <CollapsibleSection title="Worst quadrant — avoid / unwind (sample)" count={{{len(ROWS["avoid_rows"])}}} defaultOpen={{{{false}}}}>
        <Table
          headers={{["Industry", "Sector", "Med 1W", "Edge Δ med5", "Rotation", "Lane med5", "Shortlist", "Top ticker"]}}
          rows={{avoidRows}}
          striped
        />
      </CollapsibleSection>

      <Divider />

      <H2>Trade framing</H2>
      <Grid columns={{2}} gap={{16}}>
        <Card>
          <CardHeader>Tier A — express the rotation</CardHeader>
          <CardBody>
            <Stack gap={{6}}>
              <Text>Early rotation: Packaged Software (PGY, DOCS, FRSH), Advertising/Marketing (SBET), Medical Specialties.</Text>
              <Text>Blindspot rotation: Biotechnology (CLDX, COGT), Pharma Major (NUVL, INCY), Medical/Nursing (NEO, AVAH).</Text>
              <Text>Confirmed momentum: Personnel Services, Managed Health Care, Oil Refining (price-led — size smaller).</Text>
            </Stack>
          </CardBody>
        </Card>
        <Card>
          <CardHeader>Tier B — reduce / hedge</CardHeader>
          <CardBody>
            <Stack gap={{6}}>
              <Text>Semiconductors (-4.3% 1W), Electrical Products, Electronic Production Equipment, Computer Processing Hardware.</Text>
              <Text>69 industries in avoid/unwind quadrant — do not add broad beta in these lanes.</Text>
              <Text>If semis: MU, RMBS, INTC only — stock-level, not sector ETF.</Text>
            </Stack>
          </CardBody>
        </Card>
      </Grid>

      <Text tone="secondary" size="small">
        Sources: edge_latest_500m_full_parent ×10 runs · tradingview_all_fields_13_07_2026 · blindspot + unified +
        screen from ce6f36f2. Re-run scripts_adhoc/_tmp_full_market_rotation_analysis.py to refresh.
      </Text>
    </Stack>
  );
}}
'''

OUT.write_text(header + constants + body, encoding="utf-8")
print(f"Wrote {OUT}")
