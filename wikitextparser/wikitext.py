﻿"""Define the Wikitext and SubWikiText classes."""

# Todo: consider using a tree structure.
# Todo: Consider using seperate strings for each node.


import regex
from copy import deepcopy
from typing import (
    MutableSequence, Dict, List, Tuple, Union, Callable, Generator
)

from wcwidth import wcswidth

from .spans import (
    parse_to_spans,
    VALID_EXTLINK_CHARS_PATTERN,
    VALID_EXTLINK_SCHEMES_PATTERN,
    BARE_EXTERNALLINK_PATTERN
)


# HTML
HTML_TAG_REGEX = regex.compile(
    r'<([A-Z][A-Z0-9]*)\b[^>]*>(.*?)</\1>',
    regex.DOTALL | regex.IGNORECASE,
)
# External links
BRACKET_EXTERNALLINK_PATTERN = (
    r'\[' + VALID_EXTLINK_SCHEMES_PATTERN + VALID_EXTLINK_CHARS_PATTERN +
    r' *[^\]\n]*\]'
)
EXTERNALLINK_REGEX = regex.compile(
    r'(' + BARE_EXTERNALLINK_PATTERN + r'|' +
    BRACKET_EXTERNALLINK_PATTERN + r')',
    regex.IGNORECASE,
)
# Todo: Perhaps the following regular expressions could be improved by using
# the features of the regex module.
# Sections
SECTION_HEADER_REGEX = regex.compile(r'^=[^\n]+?= *$', regex.M)
LEAD_SECTION_REGEX = regex.compile(
    r'.*?(?=' + SECTION_HEADER_REGEX.pattern + r'|\Z)',
    regex.DOTALL | regex.MULTILINE,
)
SECTION_REGEX = regex.compile(
    SECTION_HEADER_REGEX.pattern + r'.*?\n*(?=' +
    SECTION_HEADER_REGEX.pattern + '|\Z)',
    regex.DOTALL | regex.MULTILINE,
)
# Tables
TABLE_REGEX = regex.compile(
    r"""
    # Table-start
    # Always starts on a new line with optional leading spaces or indentation.
    ^
    # Group the leading spaces or colons so that we can ignore them later.
    ([ :]*)
    {\| # Table contents
    # Should not contain any other table-start
    (?:
      (?!^\ *\{\|)
      .
    )*?
    # Table-end
    \n\s*
    (?:\|}|\Z)
    """,
    regex.DOTALL | regex.MULTILINE | regex.VERBOSE
)


class WikiText:

    """The WikiText class."""

    def __init__(
            self,
            string: Union[str, MutableSequence[str]],
            _type_to_spans: Dict[str, List[Tuple[int, int]]]=None,
    ) -> None:
        """Initialize the object.

        Set the initial values for self._lststr, self._type_to_spans.

        Parameters:
        - string: The string to be parsed or a list containing the string of
            the parent object.
        - _type_to_spans: If the lststr is already parsed, pass its
            _type_to_spans property as _type_to_spans to avoid parsing it
            again.

        """
        lststr = string if isinstance(string, list) else [string]
        self._lststr = lststr
        self._type_to_spans = (
            _type_to_spans if _type_to_spans else parse_to_spans(lststr[0])
        )

    def __str__(self) -> str:
        """Return self-object as a string."""
        return self.string

    def __repr__(self) -> str:
        """Return the string representation of self."""
        return '{0}({1})'.format(self.__class__.__name__, repr(self.string))

    def __contains__(self, value: Union[str, 'WikiText']) -> bool:
        """Return True if parsed_wikitext is inside self. False otherwise.

        Also self and parsed_wikitext should belong to the same parsed
        wikitext object for this function to return True.

        """
        # Is it useful (and a good practice) to also accepts str inputs
        # and check if self.string contains it?
        if isinstance(value, str):
            return value in self.string
        # isinstance(value, WikiText)
        if self._lststr is not value._lststr:
            return False
        ps, pe = value._span
        ss, se = self._span
        if ss <= ps and se >= pe:
            return True
        return False

    def __len__(self):
        return len(self.string)

    def __getitem__(self, key: Union[slice, int]) -> str:
        """Return self.string[item]."""
        return self.string[key]

    def _check_index(self, key: Union[slice, int]) -> (int, int):
        """Return adjusted start and stop index as tuple.

        Used in  __setitem__ and __delitem__.

        """
        ss, se = self._span
        if isinstance(key, int):
            if key < 0:
                key += se - ss
                if key < 0:
                    raise IndexError('index out of range')
            elif key >= se - ss:
                raise IndexError('index out of range')
            start = ss + key
            return start, start + 1
        # isinstance(key, slice)
        if key.step is not None:
            raise NotImplementedError(
                'step is not implemented for string setter.'
            )
        start, stop = key.start or 0, key.stop
        if start < 0:
            start += se - ss
            if start < 0:
                raise IndexError('start index out of range')
        if stop is None:
            stop = se - ss
        elif stop < 0:
            stop += se - ss
        if start > stop:
            raise IndexError(
                'stop index out of range or start is after the stop'
            )
        return start + ss, stop + ss

    def __setitem__(self, key: Union[slice, int], value: str) -> None:
        """Set a new string for the given slice or character index.

        Use this method instead of calling `insert` and `del` consecutively.
        By doing so only one of the `_extend_span_update` and
        `_shrink_span_update` functions will be called and the performance
        will improve.

        """
        start, stop = self._check_index(key)
        # Update lststr
        lststr = self._lststr
        lststr0 = lststr[0]
        lststr[0] = lststr0[:start] + value + lststr0[stop:]
        # Set the length of all subspans to zero because
        # they are all being replaced.
        self._close_subspans(start, stop)
        # Update the other spans according to the new length.
        del_len = stop - start
        ins_len = len(value)
        if ins_len > del_len:
            self._extend_span_update(
                estart=start,
                elength=ins_len - del_len,
            )
        elif ins_len < del_len:
            self._shrink_span_update(
                rmstart=stop + ins_len - del_len,  # new stop
                rmstop=stop,  # old stop
            )
        # Add the newly added spans contained in the value.
        spans_dict = self._type_to_spans
        for k, v in parse_to_spans(value).items():
            spans = spans_dict[k]
            for s, e in v:
                spans.append((s + start, e + start))

    def __delitem__(self, key: Union[slice, int]) -> None:
        """Remove the specified range or character from self.string.

        Note: If an operation involves both insertion and deletion, it'll be
        safer to use the `insert` function first. Otherwise there is a
        possibility of insertion into the wrong spans.

        """
        start, stop = self._check_index(key)
        lststr = self._lststr
        lststr0 = lststr[0]
        # Update lststr
        lststr[0] = lststr0[:start] + lststr0[stop:]
        # Update spans
        self._shrink_span_update(
            rmstart=start,
            rmstop=stop,
        )

    # Todo: def __add__(self, other) and __radd__(self, other)

    def insert(self, index: int, string: str) -> None:
        """Insert the given string before the specified index.

        This method has the same effect as ``self[index:index] = string``;
        it only avoids some condition checks as it rules out the possibility
        of the key being an slice, or the need to shrink any of the sub-spans.

        """
        ss, se = self._span
        lststr = self._lststr
        lststr0 = lststr[0]
        if index < 0:
            index += se - ss
            if index < 0:
                index = 0
        elif index > se - ss:  # Note that it is not >=. Index can be new.
            index = se - ss
        index += ss
        # Update lststr
        lststr[0] = lststr0[:index] + string + lststr0[index:]
        # Update spans
        self._extend_span_update(
            estart=index,
            elength=len(string),
        )
        # Remember newly added spans by the string.
        spans_dict = self._type_to_spans
        for k, v in parse_to_spans(string).items():
            spans = spans_dict[k]
            for s, e in v:
                spans.append((s + index, e + index))

    @property
    def string(self) -> str:
        """Return str(self)."""
        start, end = self._span
        return self._lststr[0][start:end]

    @string.setter
    def string(self, newstring: str) -> None:
        """Set a new string for this object. Note the old data will be lost."""
        self[0:] = newstring

    @property
    def _span(self) -> Tuple[int, int]:
        """Return self-span."""
        return 0, len(self._lststr[0])

    def _atomic_partition(
        self, char: str
    ) -> Tuple[str, str, str]:
        """Partition self.string where `char`'s not in atomic subspans."""
        shadow = self._shadow
        string = self.string
        index = shadow.find(char)
        if index == -1:
            return string, '', ''
        return string[:index], char, string[index + 1:]

    def _atomic_split_spans(
        self, char: str
    ) -> List[Tuple[int, int]]:
        """Like _not_in_atomic_subspans_split but return spans."""
        shadow = self._shadow
        ss, se = self._span
        results = []
        findstart = 0
        while True:
            index = shadow.find(char, findstart)
            if index == -1:
                results.append((ss + findstart, se))
                return results
            results.append((ss + findstart, ss + index))
            findstart = index + 1

    def _in_atomic_subspans_factory(
        self, ss: int, se: int
    ) -> Callable[[int], bool]:
        """Return a function that can tell if an index is in atomic subspans.

        Atomic sub-spans are those which are parsed separately. They currently
        include the following:
            (
                'Template', 'Parameter', 'ParserFunction',
                'Wikilink', 'Comment', 'ExtTag'
            )

        `ss` and `se` indicate the spanstart and spanend of the current span.
            If not specified, use self._span.

        The resultant function will mostly be used for splitting template
        arguments with "|" or "=" as a separator.

        The following functions depend on this function:
            * _atomic_partition
            * _not_in_atomic_subspans_split
            * _atomic_split_spans

        """
        subspans = []
        types_to_spans = self._type_to_spans
        for key in (
            'Template', 'Parameter', 'ParserFunction',
            'WikiLink', 'Comment', 'ExtTag'
        ):
            for span in types_to_spans[key]:
                if ss < span[0] and span[1] <= se:
                    subspans.append(span)

        # Define the function to be returned.
        # Todo: index_in_spans can be cached.
        def index_in_spans(index: int) -> bool:
            """Return True if the given index belongs to a sub-span."""
            for ss, se in subspans:
                if ss <= index < se:
                    return True
            return False

        return index_in_spans

    def _gen_subspan_indices(self, type_: str):
        """Yield all the subspan indices including self._span."""
        s, e = self._span
        for i, (ss, ee) in enumerate(self._type_to_spans[type_]):
            # Include self._span
            if s <= ss and ee <= e:
                yield i

    def _close_subspans(self, start: int, stop: int) -> None:
        """Close all subspans of (start, stop)."""
        ss, se = self._span
        for type_spans in self._type_to_spans.values():
            for i, (s, e) in enumerate(type_spans):
                if (start <= s and e <= stop) and (ss != s or se != e):
                    type_spans[i] = (-1, -1)

    def _shrink_span_update(self, rmstart: int, rmstop: int) -> None:
        """Update self._type_to_spans according to the removed span.

        Warning: If an operation involves both _shrink_span_update and
        _extend_span_update, you might wanna consider doing the
        _extend_span_update before the _shrink_span_update as this function
        can cause data loss in self._type_to_spans.

        """
        # Note: No span should be removed from _type_to_spans.
        rmlength = rmstop - rmstart
        for t, spans in self._type_to_spans.items():
            for i, (s, e) in enumerate(spans):
                if e <= rmstart:
                    # s <= e <= rmstart <= rmstop
                    continue
                elif rmstop <= s:
                    # rmstart <= rmstop <= s <= e
                    spans[i] = (s - rmlength, e - rmlength)
                elif rmstart <= s:
                    # s needs to be changed.
                    # We already know that rmstop is after the s,
                    # therefore the new s should be located at rmstart.
                    if rmstop >= e:
                        # rmstart <= s <= e < rmstop
                        spans[i] = (rmstart, rmstart)
                    else:
                        # rmstart < s <= rmstop <= e
                        spans[i] = (rmstart, e - rmlength)
                else:
                    # From the previous comparison we know that s is before
                    # the rmstart; so s needs no change.
                    if rmstop < e:
                        # s <= rmstart <= rmstop <= e
                        spans[i] = (s, e - rmlength)
                    else:
                        # s <= rmstart <= e < rmstop
                        spans[i] = (s, rmstart)

    def _extend_span_update(self, estart: int, elength: int) -> None:
        """Update self._type_to_spans according to the added span."""
        # Note: No span should be removed from _type_to_spans.
        ss, se = self._span
        for spans in self._type_to_spans.values():
            for i, (s, e) in enumerate(spans):
                if estart < s or (
                    # Not at the beginning of selfspan
                    estart == s != ss and e != se
                ):
                    # Added part is before the span
                    spans[i] = (s + elength, e + elength)
                elif s < estart < e or (
                    # At the end of selfspan
                    estart == s == ss and e == se
                ) or (
                    estart == e == se and s == ss
                ):
                    # Added part is inside the span
                    spans[i] = (s, e + elength)

    @property
    def _indent_level(self) -> int:
        """Calculate the indent level for self.pprint function.

        Minimum returned value for templates and parser functions is 1.
        Being part of any Template or ParserFunction increases the indent
        level by one.

        """
        ss, se = self._span
        level = 1  # a template is always found in itself
        for s, e in self._type_to_spans['Template']:
            if s < ss and se < e:
                level += 1
        for s, e in self._type_to_spans['ParserFunction']:
            if s < ss and se < e:
                level += 1
        return level

    @property
    def _shadow(self) -> str:
        """Return a copy of self.string with specified subspans replaced.

        Subspans are replaced by a block of spaces of the same size.
        This function is called upon extracting tables or extracting the data
        inside them.

        The replaced subspans are:
            ('Template', 'WikiLink', 'ParserFunction', 'ExtTag', 'Comment',)

        """
        ss, se = self._span
        string = self.string
        cached_string, cached_shadow = getattr(
            self, '_shadow_cache', (None, None)
        )
        if cached_string == string:
            return cached_shadow
        shadow = string
        for type_ in (
            'Template', 'WikiLink', 'ParserFunction', 'ExtTag', 'Comment',
            'Parameter'
        ):
            for s, e in self._type_to_spans[type_]:
                if s < ss or e > se or (s == ss and e == se):
                    continue
                shadow = (
                    shadow[:s - ss] +
                    (e - s) * ' ' +
                    shadow[e - ss:]
                )
        self._shadow_cache = (string, shadow)
        return shadow

    def _pp_type_to_spans(self) -> dict:
        """Create the arguments for the parse function used in pprint method.


        Only pass the spans of subspans and change the spans to fit the new
        scope, i.e self.string.

        """
        ss, se = self._span
        if ss == 0 and se == len(self._lststr[0]):
            return deepcopy(self._type_to_spans)
        type_to_spans = {}
        for type_, spans in self._type_to_spans.items():
            newspans = type_to_spans[type_] = []
            for s, e in spans:
                if s < ss or e > se:
                    # This line is actually covered in tests, but
                    # peephole optimization prevents it from being detected.
                    # See: http://bugs.python.org/issue2506
                    continue
                newspans.append((s - ss, e - ss))
        return type_to_spans

    def pprint(self, indent: str='    ', remove_comments=False) -> str:
        """Return a pretty-print of self.string as string.

        Try to organize templates and parser functions by indenting, aligning
        at the equal signs, and adding space where appropriate.

        Note that this function will not mutate self.

        """
        # Do not try to do inplace pprint. It will overwrite on some spans.
        parsed = WikiText(self.string, self._pp_type_to_spans())
        if remove_comments:
            for c in parsed.comments:
                c.string = ''
        else:
            # Only remove comments that contain whitespace.
            for c in parsed.comments:  # type: Comment
                if not c.contents.strip():
                    c.string = ''
        # First remove all current spacings.
        for template in parsed.templates:
            template_name = template.name.strip()
            template.name = template_name
            if ':' in template_name:
                # Don't use False because we don't know for sure.
                not_a_parser_function = None
            else:
                not_a_parser_function = True
            args = template.arguments
            if not args:
                continue
            # Required for alignment
            arg_stripped_names = [a.name.strip() for a in args]
            arg_positionalities = [a.positional for a in args]
            arg_name_lengths = [
                wcswidth(n.replace('لا', 'ل')) if
                not arg_positionalities[i] else 0 for
                i, n in enumerate(arg_stripped_names)
            ]
            max_name_len = max(arg_name_lengths)
            # Format template.name.
            level = template._indent_level
            newline_indent = '\n' + indent * level
            if level == 1:
                last_comment_indent = '<!--\n' + indent * (level - 1) + '-->'
            else:
                last_comment_indent = '<!--\n' + indent * (level - 2) + ' -->'
            template.name += newline_indent
            # Special formatting for the last argument.
            last_arg = args.pop()
            last_is_positional = arg_positionalities.pop()
            last_arg_stripped_name = arg_stripped_names.pop()
            last_arg_value = last_arg.value
            last_arg_stripped_value = last_arg_value.strip()
            if (
                not last_is_positional or
                last_arg_value == last_arg_stripped_value
            ):
                if not_a_parser_function:
                    stop_conversion = False
                    last_arg.name = (
                        ' ' + last_arg_stripped_name + ' ' +
                        ' ' * (max_name_len - arg_name_lengths.pop())
                    )
                    last_arg.value = (
                        ' ' + last_arg_stripped_value + '\n' +
                        indent * (level - 1)
                    )
                else:
                    stop_conversion = True
                    if last_is_positional:
                        # Can't strip or adjust the position of the value
                        # because this could be a positional argument in a
                        # template.
                        last_arg.value = (
                            last_arg_value + last_comment_indent
                        )
                    else:
                        # This is either a parser function or a keyword
                        # argument in a template. In both cases the name
                        # can be lstripped and the value can be rstripped.
                        last_arg.name = ' ' + last_arg.name.lstrip()
                        last_arg.value = (
                            last_arg_value.rstrip() + ' ' + last_comment_indent
                        )
            else:
                stop_conversion = True
                last_arg.value += last_comment_indent
            if not args:
                continue
            comment_indent = '<!--\n' + indent * (level - 1) + ' -->'
            for i, arg in enumerate(reversed(args)):
                i = -i - 1
                stripped_name = arg_stripped_names[i]
                positional = arg_positionalities[i]
                value = arg.value
                stripped_value = value.strip()
                # Positional arguments of templates are sensitive to
                # whitespace. See:
                # https://meta.wikimedia.org/wiki/Help:Newlines_and_spaces
                if not stop_conversion:
                    if not positional or value == stripped_value:
                        if not_a_parser_function:
                            arg.name = (
                                ' ' + stripped_name + ' ' +
                                ' ' * (max_name_len - arg_name_lengths[i])
                            )
                            arg.value = (
                                ' ' + stripped_value + newline_indent
                            )
                    else:
                        stop_conversion = True
                        arg.value += comment_indent
                else:
                    arg.value += comment_indent
        i = 0
        functions = parsed.parser_functions
        while i < len(functions):
            function = functions[i]
            i += 1
            name = function.name.lstrip()
            if name.lower() in ('#tag', '#invoke', ''):
                # The 2nd argument of `tag` parser function is an exception
                # and cannot be stripped.
                # So in `{{#tag:tagname|arg1|...}}`, no whitespace should be
                # added/removed to/from arg1.
                # See: [[mw:Help:Extension:ParserFunctions#Miscellaneous]]
                # All args of #invoke are also whitespace-sensitive.
                # Todo: Instead use comments to indent.
                continue
            args = function.arguments
            # Whitespace, including newlines, tabs, and spaces is stripped
            # from the beginning and end of all the parameters of
            # parser functions. See:
            # www.mediawiki.org/wiki/Help:Extension:ParserFunctions#
            #    Stripping_whitespace
            level = function._indent_level
            newline_indent = '\n' + indent * level
            if len(args) == 1:
                arg = args[0]
                # The first arg is both the first and last argument.
                if arg.positional:
                    arg.value = (
                        newline_indent + arg.value.strip() +
                        newline_indent.replace(indent, '', 1)
                    )
                else:
                    # Note that we don't add spaces before and after the
                    # '=' in parser functions because it could be part of
                    # an ordinary string.
                    arg.name = newline_indent + arg.name.lstrip()
                    arg.value = (
                        arg.value.rstrip() +
                        newline_indent.replace(indent, '', 1)
                    )
            else:
                # Special formatting for the first argument
                arg = args[0]
                if arg.positional:
                    arg.value = (
                        newline_indent + arg.value.strip() + newline_indent
                    )
                else:
                    arg.name = newline_indent + arg.name.lstrip()
                    arg.value = arg.value.rstrip() + newline_indent
                # Formatting the middle arguments
                for arg in args[1:-1]:
                    if arg.positional:
                        arg.value = (
                            ' ' + arg.value.strip() + newline_indent
                        )
                    else:
                        arg.name = ' ' + arg.name.lstrip()
                        arg.value = (
                            arg.value.rstrip() + newline_indent
                        )
                # Special formatting for the last argument
                arg = args[-1]
                newline_indent = newline_indent.replace(indent, '', 1)
                if arg.positional:
                    arg.value = (
                        ' ' + arg.value.strip() + newline_indent
                    )
                else:
                    arg.name = ' ' + arg.name.lstrip()
                    arg.value = arg.value.rstrip() + newline_indent
            functions = parsed.parser_functions
        return parsed.string

    # Todo: Isn't it better to use generators for the following properties?
    @property
    def parameters(self) -> List['Parameter']:
        """Return a list of parameter objects."""
        return [
            Parameter(
                self._lststr,
                self._type_to_spans,
                index,
            ) for index in self._gen_subspan_indices('Parameter')
        ]

    @property
    def parser_functions(self) -> List['ParserFunction']:
        """Return a list of parser function objects."""
        return [
            ParserFunction(
                self._lststr,
                self._type_to_spans,
                index,
            ) for index in self._gen_subspan_indices('ParserFunction')
        ]

    @property
    def templates(self) -> List['Template']:
        """Return a list of templates as template objects."""
        return [
            Template(
                self._lststr,
                self._type_to_spans,
                index,
            ) for index in self._gen_subspan_indices('Template')
        ]

    @property
    def wikilinks(self) -> List['WikiLink']:
        """Return a list of wikilink objects."""
        return [
            WikiLink(
                self._lststr,
                self._type_to_spans,
                index,
            ) for index in self._gen_subspan_indices('WikiLink')
        ]

    @property
    def comments(self) -> List['Comment']:
        """Return a list of comment objects."""
        return [
            Comment(
                self._lststr,
                self._type_to_spans,
                index,
            ) for index in self._gen_subspan_indices('Comment')
        ]

    @property
    def external_links(self) -> List['ExternalLink']:
        """Return a list of found external link objects."""
        external_links = []
        type_to_spans = self._type_to_spans
        lststr = self._lststr
        ss, se = self._span
        if 'ExternalLink' not in type_to_spans:
            # All the added spans will be new.
            spans = type_to_spans['ExternalLink'] = []
            index = 0
            for m in EXTERNALLINK_REGEX.finditer(self.string):
                mspan = m.span()
                mspan = (mspan[0] + ss, mspan[1] + ss)
                spans.append(mspan)
                external_links.append(
                    ExternalLink(lststr, type_to_spans, index)
                )
                index += 1
            return external_links
        # There are already some ExternalLink spans. Use the already existing
        # ones when the detected span is one of those.
        spans = type_to_spans['ExternalLink']
        index = len(spans) - 1
        existing_span_to_index = {s: i for i, s in enumerate(spans)}
        for m in EXTERNALLINK_REGEX.finditer(self.string):
            mspan = m.span()
            mspan = (mspan[0] + ss, mspan[1] + ss)
            mindex = existing_span_to_index.get(mspan)
            if mindex is None:
                spans.append(mspan)
                index += 1
                mindex = index
            external_links.append(
                ExternalLink(lststr, type_to_spans, mindex)
            )
        return external_links

    @property
    def sections(self) -> List['Section']:
        """Return a list of section in current wikitext.

        The first section will always be the lead section, even if it is an
        empty string.

        """
        sections = []
        type_to_spans = self._type_to_spans
        lststr = self._lststr
        ss, se = self._span
        selfstring = self.string
        if 'Section' not in type_to_spans:
            # All the added spans will be new.
            spans = type_to_spans['Section'] = []
            # Lead section
            mspan = LEAD_SECTION_REGEX.match(selfstring).span()
            mspan = (mspan[0] + ss, mspan[1] + ss)
            spans.append(mspan)
            sections.append(Section(lststr, type_to_spans, 0))
            index = 1
            # Other sections
            for m in SECTION_REGEX.finditer(selfstring):
                mspan = m.span()
                mspan = (mspan[0] + ss, mspan[1] + ss)
                spans.append(mspan)
                current_section = Section(
                    lststr, type_to_spans, index
                )
                # Add text of the current_section to any parent section.
                # Note that section 0 is not a parent for any subsection.
                current_level = current_section.level
                for section in reversed(sections[1:]):
                    section_level = section.level
                    if section_level < current_level:
                        si = section._index
                        spans[si] = (spans[si][0], mspan[1])
                        current_level = section_level
                sections.append(current_section)
                index += 1
            return sections
        # There are already some spans. Instead of appending new spans
        # use them when the detected span already exists.
        spans = type_to_spans['Section']
        index = len(spans) - 1
        existing_span_to_index = {s: i for i, s in enumerate(spans)}
        # Lead section
        mspan = LEAD_SECTION_REGEX.match(selfstring).span()
        mspan = (mspan[0] + ss, mspan[1] + ss)
        mindex = existing_span_to_index.get(mspan)
        if mindex is None:
            spans.append(mspan)
            index += 1
            mindex = index
        sections.append(
            Section(lststr, type_to_spans, mindex)
        )
        # Adjust other sections
        for m in SECTION_REGEX.finditer(selfstring):
            mspan = m.span()
            mspan = (mspan[0] + ss, mspan[1] + ss)
            mindex = existing_span_to_index.get(mspan)
            if mindex is None:
                spans.append(mspan)
                index += 1
                mindex = index
            current_section = Section(lststr, type_to_spans, mindex)
            # Add text of the current_section to any parent section.
            # Note that section 0 is not a parent for any subsection.
            current_level = current_section.level
            for section in reversed(sections[1:]):
                section_level = section.level
                if section_level < current_level:
                    si = section._index
                    spans[si] = (spans[si][0], mspan[1])
                    current_level = section_level
            sections.append(current_section)
        return sections

    @property
    def tables(self) -> List['Table']:
        """Return a list of found table objects."""
        tables = []
        type_to_spans = self._type_to_spans
        lststr = self._lststr
        shadow = self._shadow
        ss, se = self._span
        if 'Table' not in type_to_spans:
            # All the added spans will be new.
            spans = type_to_spans['Table'] = []
            index = 0
            m = True
            while m:
                m = False
                for m in TABLE_REGEX.finditer(shadow):
                    ms, me = m.span()
                    # Ignore leading whitespace using len(m.group(1)).
                    mspan = (ss + ms + len(m.group(1)), ss + me)
                    spans.append(mspan)
                    tables.append(Table(lststr, type_to_spans, index))
                    index += 1
                    shadow = shadow[:ms] + '_' * (me - ms) + shadow[me:]
            return tables
        # There are already exists some spans. Try to use the already existing
        # before appending new spans.
        spans = type_to_spans['Table']
        index = len(spans) - 1
        existing_span_to_index = {s: i for i, s in enumerate(spans)}
        m = True
        while m:
            m = False
            for m in TABLE_REGEX.finditer(shadow):
                ms, me = m.span()
                # Ignore leading whitespace using len(m.group(1)).
                mspan = (ss + ms + len(m.group(1)), ss + me)
                mindex = existing_span_to_index.get(mspan)
                if mindex is None:
                    spans.append(mspan)
                    index += 1
                    mindex = index
                tables.append(Table(lststr, type_to_spans, mindex))
                shadow = shadow[:ms] + '_' * (me - ms) + shadow[me:]
        return tables

    def lists(self, pattern: str=None) -> List['WikiList']:
        """Return a list of WikiList objects.

        :pattern: The starting pattern for list items.
            Return all types of lists (ol, ul, and dl) if pattern is None.
            If pattern is not None, it will be passed to the regex engine with
            VERBOSE flag on, so `#` and `*` should be escaped. Examples:

                - `\#` means top-level ordered lists
                - `\#\*` means unordred lists inside an ordered one
                - Currently definition lists are not well supported, but you
                    can use `[:;]` as their pattern.

            Tips and tricks:

                Be careful when using the following patterns as they will
                probably cause malfunction in the `sublists` method of the
                resultant List. (However they should be safe to use if you are
                not going to use the `sublists` method.)

                - Use `\*+` as a pattern and nested unordered lists will be
                    treated as flat.
                - Use `\*\s*` as pattern to rtstrip `items` of the list.

                Although this parameter is optional, but specifying it can
                improve the performance.

        """
        lists = []
        lststr = self._lststr
        type_to_spans = self._type_to_spans
        spans = type_to_spans.setdefault('WikiList', [])
        patterns = ('\#', '\*', '[:;]') if pattern is None else (pattern,)
        for pattern in patterns:
            list_regex = regex.compile(
                LIST_PATTERN.format(pattern=pattern),
                regex.MULTILINE | regex.VERBOSE,
            )
            ss = self._span[0]
            for index, m in enumerate(list_regex.finditer(self._shadow)):
                ms, me = m.span()
                span = ss + ms, ss + me
                index = next((i for i, s in enumerate(spans) if s == span), None)
                if index is None:
                    index = len(spans)
                    spans.append(span)
                lists.append(
                    WikiList(
                        lststr, pattern, m, type_to_spans, index, 'WikiList'
                    )
                )
        return lists


class SubWikiText(WikiText):

    """Define a middle-class to be used by some other subclasses.

    Allow the user to focus on a particular part of WikiText.

    """

    def __init__(
        self,
        string: Union[str, MutableSequence[str]],
        _type_to_spans: Dict[str, List[Tuple[int, int]]]=None,
        _index: int=None,
        _type: str=None,
    ) -> None:
        """Initialize the object.

        Run self._common_init.
        Set self._index

        """
        _type = _type or self.__class__.__name__
        self._type = _type

        super().__init__(string, _type_to_spans)

        # Note: _type_to_spans and _index are either both None or not None.
        if _type_to_spans is None and _type not in {
            'Parameter',
            'ParserFunction',
            'Template',
            'WikiLink',
            'Comment',
            'ExtTag',
        }:
            self._type_to_spans[_type] = [(0, len(string))]
            self._index = 0
        else:
            self._index = len(
                self._type_to_spans[_type]
            ) - 1 if _index is None else _index

    def _gen_subspan_indices(
        self, _type: str=None
    ) -> Generator[int, None, None]:
        """Yield all the subspan indices excluding self._span."""
        s, e = self._span
        for i, (ss, ee) in enumerate(self._type_to_spans[_type]):
            # Do not yield self._span.
            if s < ss and ee <= e:
                yield i

    @property
    def _span(self) -> Tuple[int, int]:
        """Return the span of self."""
        return self._type_to_spans[self._type][self._index]
