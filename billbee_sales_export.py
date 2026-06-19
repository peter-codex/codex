#!/usr/bin/env python3
"""Export Billbee order/sales data to CSV or JSON.

Credentials are read from environment variables by default:
  BILLBEE_API_KEY
  BILLBEE_USERNAME
  BILLBEE_API_PASSWORD
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


DEFAULT_BASE_URL = "https://api.billbee.io/api/v1"

BILLBEE_API_KEY = ""
BILLBEE_USERNAME = ""
BILLBEE_API_PASSWORD = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export sales/orders from Billbee using API key + Basic Auth."
    )
    parser.add_argument("--api-key", default=os.getenv("BILLBEE_API_KEY") or BILLBEE_API_KEY)
    parser.add_argument("--username", default=os.getenv("BILLBEE_USERNAME") or BILLBEE_USERNAME)
    parser.add_argument(
        "--api-password",
        default=os.getenv("BILLBEE_API_PASSWORD") or BILLBEE_API_PASSWORD,
    )
    parser.add_argument("--base-url", default=os.getenv("BILLBEE_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--output", "-o", default="billbee_sales_export.csv")
    parser.add_argument("--format", choices=("csv", "json"), default="csv")
    parser.add_argument(
        "--list-fields",
        action="store_true",
        help="Only print available Billbee fields from the fetched sample and do not export data.",
    )
    parser.add_argument(
        "--rows",
        choices=("orders", "positions", "all"),
        default="positions",
        help="CSV row shape: selected order fields, selected position fields, or all fields as columns.",
    )
    parser.add_argument("--min-order-date", help="Oldest order date, e.g. 2026-01-01T00:00:00")
    parser.add_argument("--max-order-date", help="Newest order date, e.g. 2026-01-31T23:59:59")
    parser.add_argument("--modified-at-min", help="Only orders modified after this date-time.")
    parser.add_argument("--modified-at-max", help="Only orders modified before/equal this date-time.")
    parser.add_argument("--minimum-billbee-order-id", type=int)
    parser.add_argument("--shop-id", action="append", type=int, default=[])
    parser.add_argument("--order-state-id", action="append", type=int, default=[])
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--exclude-tags", action="store_true")
    parser.add_argument(
        "--platform",
        action="append",
        default=[],
        help="Filter exported orders by Seller.Platform, e.g. Etsy, Amazon, Kasuwa or eBay. Can be used multiple times.",
    )
    parser.add_argument("--page-size", type=int, default=250)
    parser.add_argument("--max-pages", type=int, help="Optional safety limit for test exports.")
    parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="Timeout per Billbee request in seconds.",
    )
    return parser.parse_args()


def require_credentials(args: argparse.Namespace) -> None:
    missing = [
        name
        for name, value in (
            ("BILLBEE_API_KEY/--api-key", args.api_key),
            ("BILLBEE_USERNAME/--username", args.username),
            ("BILLBEE_API_PASSWORD/--api-password", args.api_password),
        )
        if not value
    ]
    if missing:
        raise SystemExit("Missing credentials: " + ", ".join(missing))


def first_value(source: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return default


class BillbeeClient:
    def __init__(self, base_url: str, api_key: str, username: str, api_password: str) -> None:
        self.base_url = base_url.rstrip("/")
        auth_token = base64.b64encode(f"{username}:{api_password}".encode("utf-8")).decode("ascii")
        self.headers = {
            "Accept": "application/json",
            "X-Billbee-Api-Key": api_key,
            "Authorization": f"Basic {auth_token}",
            "User-Agent": "billbee-sales-export/1.0",
        }

    def get(self, path: str, params: list[tuple[str, Any]], timeout: int = 180) -> dict[str, Any]:
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params, doseq=True)}"
        request = urllib.request.Request(url, headers=self.headers, method="GET")

        for attempt in range(5):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    retry_after = int(exc.headers.get("Retry-After", "2"))
                    time.sleep(retry_after + 0.2)
                    continue
                body = exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Billbee API returned HTTP {exc.code}: {body}") from exc
            except socket.timeout:
                if attempt == 4:
                    raise RuntimeError(
                        "Billbee API timed out repeatedly. Try --page-size 50 or a smaller date range."
                    )
                time.sleep(2**attempt)
            except urllib.error.URLError as exc:
                if attempt == 4:
                    raise RuntimeError(f"Could not reach Billbee API: {exc}") from exc
                time.sleep(2**attempt)

        raise RuntimeError("Billbee API retry limit exceeded.")


def build_order_params(args: argparse.Namespace, page: int) -> list[tuple[str, Any]]:
    params: list[tuple[str, Any]] = [("page", page), ("pageSize", min(max(args.page_size, 1), 250))]
    optional = {
        "minOrderDate": args.min_order_date,
        "maxOrderDate": args.max_order_date,
        "modifiedAtMin": args.modified_at_min,
        "modifiedAtMax": args.modified_at_max,
        "minimumBillBeeOrderId": args.minimum_billbee_order_id,
    }
    params.extend((key, value) for key, value in optional.items() if value not in (None, ""))
    params.extend(("shopId", value) for value in args.shop_id)
    params.extend(("orderStateId", value) for value in args.order_state_id)
    params.extend(("tag", value) for value in args.tag)
    if args.exclude_tags:
        params.append(("excludeTags", "true"))
    return params


def extract_page_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("Data", payload.get("data", payload))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("Items", "items", "Orders", "orders"):
            items = data.get(key)
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
    return []


def total_pages(payload: dict[str, Any]) -> int | None:
    paging = payload.get("Paging") or payload.get("paging") or {}
    for key in ("TotalPages", "totalPages", "Pages", "pages"):
        value = paging.get(key) if isinstance(paging, dict) else None
        if isinstance(value, int):
            return value
    return None


def fetch_orders(client: BillbeeClient, args: argparse.Namespace) -> list[dict[str, Any]]:
    orders: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = client.get("/orders", build_order_params(args, page), timeout=args.timeout)
        items = extract_page_items(payload)
        orders.extend(items)

        known_total_pages = total_pages(payload)
        if not items or (known_total_pages and page >= known_total_pages):
            break
        if args.max_pages and page >= args.max_pages:
            break
        page += 1
        time.sleep(0.55)
    return orders


def order_platform(order: dict[str, Any]) -> str:
    seller = order.get("Seller")
    if isinstance(seller, dict):
        platform = seller.get("Platform")
        if platform:
            return str(platform)
    api_account = order.get("ApiAccountName")
    return str(api_account) if api_account else ""


def filter_orders(orders: list[dict[str, Any]], platforms: list[str] | None = None) -> list[dict[str, Any]]:
    if not platforms:
        return orders
    selected = {platform.casefold() for platform in platforms}
    return [order for order in orders if order_platform(order).casefold() in selected]


def order_row(order: dict[str, Any]) -> dict[str, Any]:
    customer = first_value(order, "Customer", "Buyer", default={})
    shop = first_value(order, "ShopName", "SellerComment", "ShopId")
    if isinstance(customer, dict):
        customer_name = first_value(customer, "Name", "FullName", "Company", "Email")
        customer_email = first_value(customer, "Email", "Mail")
    else:
        customer_name = customer
        customer_email = ""

    return {
        "billbee_order_id": first_value(order, "Id", "BillBeeOrderId"),
        "order_number": first_value(order, "OrderNumber", "ExternalReference", "ExtRef"),
        "order_date": first_value(order, "CreatedAt", "OrderDate", "InvoiceDate"),
        "paid_at": first_value(order, "PayDate", "PaidAt"),
        "modified_at": first_value(order, "UpdatedAt", "ModifiedAt", "LastModifiedAt"),
        "state": first_value(order, "State", "StateId", "OrderStateId"),
        "shop": shop,
        "customer": customer_name,
        "customer_email": customer_email,
        "currency": first_value(order, "Currency", "CurrencyCode"),
        "total_gross": first_value(order, "TotalCost", "TotalGross", "TotalPrice", "Total"),
        "shipping_gross": first_value(order, "ShippingCost", "ShippingCosts"),
        "payment_method": first_value(order, "PayWay", "PaymentMethod", "PaymentType"),
    }


def position_rows(order: dict[str, Any]) -> list[dict[str, Any]]:
    base = order_row(order)
    positions = first_value(order, "OrderItems", "Items", "Positions", default=[])
    if not isinstance(positions, list) or not positions:
        return [{**base, "sku": "", "title": "", "quantity": "", "unit_price": "", "vat_rate": ""}]

    rows = []
    for position in positions:
        if not isinstance(position, dict):
            continue
        article = first_value(position, "Product", "Article", default={})
        title = first_value(position, "Title", "Name", "ProductTitle")
        sku = first_value(position, "SKU", "Sku", "ArticleNumber", "ProductId")
        if isinstance(article, dict):
            title = title or first_value(article, "Title", "Name")
            sku = sku or first_value(article, "SKU", "Sku", "ArticleNumber")
        rows.append(
            {
                **base,
                "sku": sku,
                "title": title,
                "quantity": first_value(position, "Quantity", "Qty", "Amount"),
                "unit_price": first_value(position, "TotalPrice", "Price", "UnitPrice"),
                "vat_rate": first_value(position, "TaxRate", "VatRate", "Vat"),
            }
        )
    return rows


def csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def flatten_dict(source: dict[str, Any], prefix: str = "", index_lists: bool = False) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for key, value in source.items():
        column = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            row.update(flatten_dict(value, column, index_lists=index_lists))
        elif isinstance(value, list) and index_lists:
            if not value:
                row[column] = ""
            for index, item in enumerate(value, start=1):
                item_column = f"{column}[{index}]"
                if isinstance(item, dict):
                    row.update(flatten_dict(item, item_column, index_lists=index_lists))
                elif isinstance(item, list):
                    row.update(flatten_any(item, item_column, index_lists=index_lists))
                else:
                    row[item_column] = csv_value(item)
        else:
            row[column] = csv_value(value)
    return row


def flatten_any(value: Any, prefix: str, index_lists: bool = True) -> dict[str, Any]:
    if isinstance(value, dict):
        return flatten_dict(value, prefix, index_lists=index_lists)
    if isinstance(value, list):
        row: dict[str, Any] = {}
        if not value:
            row[prefix] = ""
        for index, item in enumerate(value, start=1):
            item_column = f"{prefix}[{index}]"
            if isinstance(item, dict):
                row.update(flatten_dict(item, item_column, index_lists=index_lists))
            elif isinstance(item, list):
                row.update(flatten_any(item, item_column, index_lists=index_lists))
            else:
                row[item_column] = csv_value(item)
        return row
    return {prefix: csv_value(value)}


def all_columns_rows(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for order in orders:
        order_without_items = {key: value for key, value in order.items() if key != "OrderItems"}
        order_columns = flatten_dict(order_without_items, index_lists=True)
        order_items = order.get("OrderItems")

        if not isinstance(order_items, list) or not order_items:
            rows.append(order_columns)
            continue

        for position_index, item in enumerate(order_items, start=1):
            row = dict(order_columns)
            row["OrderItems.RowNumber"] = position_index
            if isinstance(item, dict):
                row.update(flatten_dict(item, "OrderItems", index_lists=True))
            else:
                row["OrderItems.Value"] = csv_value(item)
            rows.append(row)
    return rows


def flatten_field_paths(value: Any, prefix: str = "") -> set[str]:
    if isinstance(value, dict):
        fields: set[str] = set()
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            fields.update(flatten_field_paths(child, child_prefix))
        return fields
    if isinstance(value, list):
        fields = {prefix}
        for child in value:
            if isinstance(child, dict):
                fields.update(flatten_field_paths(child, f"{prefix}[]"))
            elif isinstance(child, list):
                fields.update(flatten_field_paths(child, f"{prefix}[]"))
        return fields
    return {prefix}


def available_fieldnames(orders: list[dict[str, Any]]) -> list[str]:
    fieldnames: list[str] = []
    seen: set[str] = set()
    for order in orders:
        for fieldname in sorted(flatten_field_paths(order)):
            if fieldname not in seen:
                seen.add(fieldname)
                fieldnames.append(fieldname)
    return fieldnames


def natural_fieldnames(rows: list[dict[str, Any]]) -> list[str]:
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    return fieldnames


def write_csv(path: str, orders: list[dict[str, Any]], rows_mode: str) -> None:
    rows: list[dict[str, Any]] = []
    if rows_mode == "all":
        rows = all_columns_rows(orders)
    else:
        for order in orders:
            if rows_mode == "positions":
                rows.extend(position_rows(order))
            else:
                rows.append(order_row(order))

    if rows_mode == "all":
        fieldnames = natural_fieldnames(rows)
    elif rows_mode == "positions":
        fieldnames = list(position_rows({})[0].keys())
    else:
        fieldnames = list(rows[0].keys()) if rows else list(order_row({}).keys())

    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: str, orders: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(orders, handle, ensure_ascii=False, indent=2)


def main() -> int:
    args = parse_args()
    require_credentials(args)
    client = BillbeeClient(args.base_url, args.api_key, args.username, args.api_password)
    orders = filter_orders(fetch_orders(client, args), args.platform)

    if args.list_fields:
        fieldnames = available_fieldnames(orders)
        for fieldname in fieldnames:
            print(fieldname)
        print(f"\nFound {len(fieldnames)} fields from {len(orders)} sampled orders.", file=sys.stderr)
        return 0

    if args.format == "json":
        write_json(args.output, orders)
    else:
        write_csv(args.output, orders, args.rows)

    print(f"Exported {len(orders)} orders to {args.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit("Interrupted.")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
