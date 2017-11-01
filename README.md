# tbxtools
 
A unified repository of libtbx-like distribution handling and
transformation tools.

After writing several projects with common handling infrastructure,
and having to manage differences in data model between them (as well
as remembering what functionality was in each repository), this
repository was created to act as a unified store of all tools, so that
they can use the same underlying data models.

This suite of tools should provide:
- Basic functionality for introspecting dependencies for e.g. CMake
- Tools to convert a tbx distribution into other build systems
- Tools to analyse the module/repository layout of a distribution

