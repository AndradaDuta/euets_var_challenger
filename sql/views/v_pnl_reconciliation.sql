-- Reconciliation of hypothetical P&L across the hierarchy.
-- One row per date and parent node: the parent's own P&L against the sum of its children.
--   desk  rows: desk P&L  vs the sum of its books
--   group rows: group P&L vs the sum of its desks
--   book  rows: book P&L  vs the sum of its attribution components
-- 'difference_eur' should be zero on every row (up to floating-point rounding).
CREATE OR REPLACE VIEW v_pnl_reconciliation AS
WITH desk_children AS (
    SELECT p.date, h.desk AS node, SUM(p.hypothetical_pnl_eur) AS children_pnl_eur, COUNT(*) AS n_children
    FROM pnl p
    JOIN hierarchy h ON p.node = h.book
    WHERE p.level = 'book'
    GROUP BY p.date, h.desk
),
group_children AS (
    SELECT p.date, g.entity AS node, SUM(p.hypothetical_pnl_eur) AS children_pnl_eur, COUNT(*) AS n_children
    FROM pnl p
    JOIN (SELECT DISTINCT desk, entity FROM hierarchy) g ON p.node = g.desk
    WHERE p.level = 'desk'
    GROUP BY p.date, g.entity
),
book_children AS (
    SELECT date, book AS node, SUM(pnl_eur) AS children_pnl_eur, COUNT(*) AS n_children
    FROM pnl_attribution
    GROUP BY date, book
),
children AS (
    SELECT 'desk' AS level, * FROM desk_children
    UNION ALL
    SELECT 'group' AS level, * FROM group_children
    UNION ALL
    SELECT 'book' AS level, * FROM book_children
)
SELECT
    p.date,
    p.level,
    p.node,
    p.hypothetical_pnl_eur                          AS node_pnl_eur,
    c.children_pnl_eur,
    c.n_children,
    p.hypothetical_pnl_eur - c.children_pnl_eur     AS difference_eur
FROM pnl p
LEFT JOIN children c
    ON p.date = c.date AND p.level = c.level AND p.node = c.node;
