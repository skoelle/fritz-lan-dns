(function () {
    const collator = new Intl.Collator("de", { numeric: true, sensitivity: "base" });

    function cellValue(cell) {
        if (cell && cell.dataset.sortValue !== undefined) {
            return cell.dataset.sortValue;
        }
        return (cell ? cell.textContent : "").trim();
    }

    function isBlank(value) {
        const trimmed = String(value).trim();
        return trimmed === "" || trimmed === "-";
    }

    function sortTable(table, th) {
        const tbody = table.tBodies[0];
        const headerRow = table.tHead && table.tHead.rows[0];
        if (!tbody || !headerRow || th.dataset.sort === "none") {
            return;
        }

        const headers = Array.from(headerRow.cells);
        const column = headers.indexOf(th);
        if (column < 0) {
            return;
        }

        const descending = th.classList.contains("sort-asc");
        const rows = Array.from(tbody.rows)
            .filter((row) => row.cells.length === headers.length)
            .map((row, index) => ({ row, index }));

        rows.sort((a, b) => {
            const left = cellValue(a.row.cells[column]);
            const right = cellValue(b.row.cells[column]);
            const leftBlank = isBlank(left);
            const rightBlank = isBlank(right);
            if (leftBlank !== rightBlank) {
                return leftBlank ? 1 : -1;
            }
            const result = collator.compare(left, right);
            if (result !== 0) {
                return descending ? -result : result;
            }
            return a.index - b.index;
        });

        rows.forEach((entry) => tbody.appendChild(entry.row));

        headers.forEach((header) => {
            header.classList.remove("sort-asc", "sort-desc");
            header.setAttribute("aria-sort", "none");
        });
        th.classList.add(descending ? "sort-desc" : "sort-asc");
        th.setAttribute("aria-sort", descending ? "descending" : "ascending");
    }

    function makeSortable(table) {
        const headerRow = table.tHead && table.tHead.rows[0];
        if (!headerRow) {
            return;
        }

        Array.from(headerRow.cells).forEach((th) => {
            if (th.dataset.sort === "none") {
                return;
            }
            th.setAttribute("role", "button");
            th.setAttribute("tabindex", "0");
            th.setAttribute("aria-sort", "none");

            th.addEventListener("click", () => sortTable(table, th));
            th.addEventListener("keydown", (event) => {
                if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    sortTable(table, th);
                }
            });
        });
    }

    function init() {
        Array.from(document.querySelectorAll("table[data-sortable]")).forEach(makeSortable);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
