from tree_sitter_language_pack import get_parser

def extract_headers_and_bodies(file_path: str) -> list[dict]:
    """
    Parses the given Python file and returns a list of dicts, each containing:
      - node_type: one of
          'import_statement', 'import_from_statement', 'future_import_statement',
          'function_definition', 'async_function_definition', 'class_definition',
          'decorated_definition', 'if_statement'
      - name: identifier for function/class (None for imports and if)
      - header: signature or full import/if line
      - body: indented suite (None for imports or if with no suite)
    """
    with open(file_path, "rb") as f:
        source_bytes = f.read()

    parser = get_parser("python")
    tree = parser.parse(source_bytes)
    root = tree.root_node  # type == "module" :contentReference[oaicite:2]{index=2}

    results = []
    for node in root.named_children:
        nt = node.type

        # 1. Imports: import_statement, import_from_statement, future_import_statement
        if nt in ("import_statement", "import_from_statement", "future_import_statement"):
            header = source_bytes[node.start_byte : node.end_byte].decode("utf8")
            results.append({
                "node_type": nt,
                "name": None,
                "header": header,
                "body": None
            })
            continue

        # 2. if __name__ == "__main__": block (only this special pattern)
        if nt == "if_statement":
            cond = node.child_by_field_name("condition")
            if cond:
                cond_text = source_bytes[cond.start_byte : cond.end_byte].decode("utf8")
                if "__name__" in cond_text:
                    body_node = node.child_by_field_name("consequence")
                    header = source_bytes[node.start_byte : body_node.start_byte].decode("utf8")
                    body   = source_bytes[body_node.start_byte : node.end_byte].decode("utf8")
                    results.append({
                        "node_type": "if_statement",
                        "name": None,
                        "header": header,
                        "body": body
                    })
                    continue

        # 3. Decorated definitions (unwrap to class or function)
        if nt == "decorated_definition":
            inner = node.child_by_field_name("definition")
            if inner:
                if inner.type == "class_definition":
                    _process_python_class(inner, source_bytes, results)
                else:
                    is_async = (inner.type == "async_function_definition")
                    _process_python_function(inner, source_bytes, results, is_async=is_async)
            continue

        # 4. Async functions
        if nt == "async_function_definition":
            _process_python_function(node, source_bytes, results, is_async=True)
            continue

        # 5. Regular functions or classes
        if nt == "function_definition":
            _process_python_function(node, source_bytes, results, is_async=False)
            continue

        if nt == "class_definition":
            _process_python_class(node, source_bytes, results)
            continue

        # 6. Everything else: skip
        continue

    return results


def _process_python_function(func_node, source_bytes: bytes, out_list: list, is_async: bool):
    """
    Given a function_definition or async_function_definition node, extract:
      - node_type: "function_definition" or "async_function_definition"
      - name: function name
      - header: the 'def …:' (including 'async' if present)
      - body: function suite (None if missing)
    """
    nt = "async_function_definition" if is_async else "function_definition"
    name_node = func_node.child_by_field_name("name")
    name = source_bytes[name_node.start_byte : name_node.end_byte].decode("utf8") if name_node else None

    body_node = func_node.child_by_field_name("body")
    if body_node:
        header = source_bytes[func_node.start_byte : body_node.start_byte].decode("utf8")
        body   = source_bytes[body_node.start_byte : func_node.end_byte].decode("utf8")
    else:
        header = source_bytes[func_node.start_byte : func_node.end_byte].decode("utf8")
        body   = None

    out_list.append({
        "node_type": nt,
        "name": name,
        "header": header,
        "body": body
    })

    # Recursively process nested definitions inside the function body
    if body_node:
        for child in body_node.named_children:
            if child.type == "function_definition":
                _process_python_function(child, source_bytes, out_list, is_async=False)
            elif child.type == "async_function_definition":
                _process_python_function(child, source_bytes, out_list, is_async=True)
            elif child.type == "class_definition":
                _process_python_class(child, source_bytes, out_list)


def _process_python_class(class_node, source_bytes: bytes, out_list: list):
    """
    Given a class_definition node, split header/body, then capture:
      - methods (function_definition, async_function_definition)
      - nested classes (class_definition)
    (We have removed assignment capture for brevity.)
    """
    nt = "class_definition"
    name_node = class_node.child_by_field_name("name")
    name = source_bytes[name_node.start_byte : name_node.end_byte].decode("utf8") if name_node else None

    body_node = class_node.child_by_field_name("body")
    if body_node:
        header = source_bytes[class_node.start_byte : body_node.start_byte].decode("utf8")
        body   = source_bytes[body_node.start_byte : class_node.end_byte].decode("utf8")
    else:
        header = source_bytes[class_node.start_byte : class_node.end_byte].decode("utf8")
        body   = None

    out_list.append({
        "node_type": nt,
        "name": name,
        "header": header,
        "body": body
    })

    # Scan class body for methods, nested classes, and decorated definitions
    if body_node:
        for member in body_node.named_children:
            mtype = member.type

            if mtype == "function_definition":
                _process_python_function(member, source_bytes, out_list, is_async=False)
                continue

            if mtype == "async_function_definition":
                _process_python_function(member, source_bytes, out_list, is_async=True)
                continue

            if mtype == "class_definition":
                _process_python_class(member, source_bytes, out_list)
                continue

            if mtype == "decorated_definition":
                inner = member.child_by_field_name("definition")
                if inner:
                    if inner.type == "class_definition":
                        _process_python_class(inner, source_bytes, out_list)
                    else:
                        is_async = (inner.type == "async_function_definition")
                        _process_python_function(inner, source_bytes, out_list, is_async=is_async)
                continue
