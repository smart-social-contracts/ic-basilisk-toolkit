"""Extra Cedar policies loaded at runtime via ``enable_public_read``.

Kept in Python so the canister bundles it (``.cedar`` files are not auto-included).
"""

PUBLIC_READ = """
// Public lists: anyone may read lists and items marked public.
permit (
  principal,
  action in [
    TodoApp::Action::"entity.get",
    TodoApp::Action::"entity.list"
  ],
  resource is TodoApp::TodoList
)
when {
  resource has public && resource.public == true
};

permit (
  principal,
  action in [
    TodoApp::Action::"entity.get",
    TodoApp::Action::"entity.list"
  ],
  resource is TodoApp::TodoItem
)
when {
  resource has todo_list
  && resource.todo_list has public
  && resource.todo_list.public == true
};
"""
