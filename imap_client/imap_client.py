from __future__ import annotations
from pathlib import Path
import re
import imaplib
import email, email.message, email.header
import datetime
import pathlib

# TODO document each function, method and class, as well as the whole module


def check_res(response):
    if response != "OK":
        raise ValueError("Bad response from connection")


def remove_newlines(string: str):
    return re.sub("\r\n|\r|\n", "", string)


def parse_list(response_data: list[bytes]) -> list:
    return response_data[0].decode().split()


def parse_list_response(response):
    return parse_list(response[1])


class Msg:
    def __init__(self, id, preview_headers: dict, inbox: Inbox):
        self.uid = id
        self.conn = inbox.conn
        self.inbox = inbox
        self.preview_headers = preview_headers
        self.save_path: pathlib.Path = inbox.save_path \
            / preview_headers["From"] \
            / preview_headers["Subject"]

        # TODO make below attributes properties, raising exception when accessed
        # before running .fetch_data()
        # raise AttributeError("Please run .fetch_data() first")
        self.raw_message = None
        self.message = None
        self.text_body = None
        self.html_body = None
        self.attachments = None
        self.date = None

    def fetch_data(self):
        self.inbox.select()
        self.message = self.get_message()
        body_parts = self.get_body(self.message)

        self.text_body = []
        self.html_body = []
        self.attachments = []
        self.parse_parts(body_parts)
        self.text_body = "\n".join(self.text_body) if self.text_body else None
        self.html_body = "\n".join(self.html_body) if self.html_body else None
        date = " ".join(self.message["date"].split()[:6])
        try:
            self.date = datetime.datetime.strptime(
                    date, "%a, %d %b %Y %H:%M:%S %z"
                ).astimezone(tz=None)
        except ValueError:
            self.date = datetime.datetime.strptime(
                    date, "%a, %d %b %Y %H:%M:%S %Z"
                ).replace(tzinfo=datetime.timezone.utc).astimezone(tz=None)

        # Decode utf-8 headers
        for i, header in enumerate(self.message._headers):
            header_name = header[0]
            header_value = email.header.decode_header(header[1])[0][0]
            try:
                header_value = header_value.decode()
            except AttributeError:
                pass
            
            self.message._headers[i] = (header_name, remove_newlines(header_value))

    def get_data(self, data_type) -> bytes:
        res, data = self.conn.uid("FETCH", str(self.uid), data_type)
        check_res(res)
        return data[0][1]

    def get_header(self, header) -> str:
        header_data = self.get_data(f"BODY[HEADER.FIELDS ({header.upper()})]")
        header_data =  email.header.decode_header(
                re.sub(header + ": ", "", header_data.decode(), flags=re.I)
            )[0][0]
        try:
            header_data = header_data.decode()
        except AttributeError:
            pass

        return header_data

    def get_message(self) -> email.message.Message:
        contents = self.get_data("RFC822")
        self.raw_message = contents.decode()
        return email.message_from_bytes(contents) 
    
    def get_body(self, message: email.message.Message) \
        -> list(tuple(str, str) | tuple(str, str, str, email.message.Message)) \
         | tuple(str, str) | tuple(str, str, str, email.message.Message):
        """
        Returns a list of message parts, each a tuple consisting of the MIMEtype
        and the payload contents in decoded from, or, in the case of an
        attachment, a tuple with a string signalising "attachment", the
        MIMEtype, the attachment filename, and a reference to that message part
        for future attachment downloading using Message.get_payload().
        In case there is only 1 message part, it returns just that part in a
        tuple.
        """
        parts = []
                                                                                
        if message.is_multipart():
            for payload in message.get_payload():
                # recursively fetch payload of parts
                parts.append(self.get_body(payload))
            return parts

        if message.get_content_maintype() != "text"\
        and message.get_content_disposition() == "attachment":
            return (message.get_content_disposition(), message.get_content_type(),
                    message.get_filename(), message)

        return  (message.get_content_type(),
                 message.get_payload(decode=True).decode())

    def parse_parts(self, body_parts: list | tuple):
        if isinstance(body_parts, tuple):
            if body_parts[0] == "text/plain":
                self.text_body.append(body_parts[1])
            elif body_parts[0] == "text/html":
                self.html_body.append(body_parts[1])
            elif body_parts[0] == "attachment":
                self.attachments.append(body_parts)
                
        elif isinstance(body_parts, list):
            for part in body_parts:
                self.parse_parts(part)

        else:
            raise TypeError("Invalid data type sent to parse")

    def save_attachment(self, attachment_index: int):
        file_path = self.save_path / self.attachments[attachment_index][2]

        if not file_path.parent.is_dir():
            file_path.parent.mkdir(parents=True)

        file_path.write_bytes(self.attachments[attachment_index][3].get_payload(
            decode=True
        ))
    
    def delete(self):
        self.inbox.delete_message(self)

    def move_to(self, inbox_index: int) -> bool:
        return self.inbox.client.move_message(self, self.inbox, inbox_index)


class MessageList:
    def __init__(self, name, search_key, query_string, **settings):
        self.name = name
        self.size = None
        self.conn = settings.get("conn")
        self.msg_generator = self._get_messages(search_key, query_string)
        self.msg_display_amount = settings.get("msg_display_amount")
        self.save_path = settings.get("save_path")
        self.messages = self.msg_generator.__next__() \
            if settings.get("auto_fetch_msgs") else []

    def select(self, name):
        self.conn.select(name)

    def select_state(function):
        def wrapper(self, *args, **kwargs):
            self.select(*args, **kwargs)
            return function(self, *args, **kwargs)
        return wrapper

    @select_state
    def get_messages(self):
        try:
            new_messages = self.msg_generator.__next__()
        except StopIteration:
            raise StopIteration("No more messages to fetch")
        else:
            self.messages += new_messages

    @select_state
    def _get_messages(self, search_key, query_string=None) -> list[Msg]:

        if self.size == 0:
            yield []

        uids = parse_list_response(
            self.conn.uid(search_key, f"{query_string if query_string else ''}")
        )

        for start in range(self.size, 0, -self.msg_display_amount):

            end = start - self.msg_display_amount
            end = end if end > 0 else 0

            fetch_amount = start - end

            to_fetch = [uids.pop() for _ in range(fetch_amount)]

            bulk_headers = {header: self._get_bulk_headers(to_fetch, header)
                    for header in ["From", "Subject"]}
                
            msgs = []
            index_in_bulk = -1
            for j in range(fetch_amount):
                msgs.append(
                    Msg(to_fetch[j], self.parse_headers(bulk_headers, index_in_bulk), self)
                )
                index_in_bulk -= 1

            yield msgs



class Inbox(MessageList):
    def __init__(self, flags, delimiter, name, client: IMAPClient, **settings):
        super().__init__(name, "ALL", None, **settings)
        self.flags = flags
        self.delimiter = delimiter
        self.update_size()
        self.client = client

    def update_size(self):
        res, size = self.select()
        check_res(res)
        size = int(size[0].decode())
        self.size = size

    def select(self):
        return self.conn.select(f'"{self.name}"')

    @select_state
    def next_uid(self):
        return self.conn.untagged_responses.get("UIDNEXT", None)


    def _get_data(self, msg_ids: str, data_type: str) -> list[bytes]:
        res, data = self.conn.uid("FETCH", msg_ids, data_type)
        check_res(res)
        return data

    get_data = select_state(_get_data)

    def _get_bulk_headers(self, to_fetch: list[str], header: str) -> list:
        headers = self._get_data(f"{','.join(to_fetch)}", f"BODY[HEADER.FIELDS ({header})]")

        if isinstance(headers[0], tuple):
            headers = [header for header in headers if isinstance(header, tuple)]
        
        return headers

    get_bulk_headers = select_state(_get_bulk_headers)

    @staticmethod
    def parse_headers(bulk_headers: dict, message_index: int) -> dict:
        headers = {
            header: email.header.decode_header(
                remove_newlines(
                    re.sub(header + ": ", "", bulk_headers[header][message_index][1] 
                    .decode(), flags=re.I).strip())
            )[0][0] \
            for header in bulk_headers.keys()
        }
        # account for different return types of email.header.decode_header()
        for key in headers.keys():
            try:
                headers[key] = headers[key].decode()
            except AttributeError:
                pass

        return headers

    @select_state
    def delete_message(self, message):
        uid = message.uid
        self.conn.uid("STORE", uid, "+FLAGS", "\\Deleted")
        self.conn.expunge()
        self.messages.remove(message)
        self.update_size()


class SearchResults(MessageList):
    def __init__(self, name: str, search_key: str, query_string: str,
                 search_in: list[Inbox], **settings):
        super().__init__(name, search_key, query_string, **settings)

    def 


class IMAPClient:
    def __init__(self, conn: imaplib.IMAP4 | imaplib.IMAP4_SSL,
                 msg_display_amount: int = 100, auto_fetch_msgs: bool = True,
                 save_path: str | pathlib.Path = "attachments"):
        self.conn = conn
        self.msg_display_amount = msg_display_amount
        self.inbox_data = {
            "conn": conn, 
            "msg_display_amount": msg_display_amount,
            "auto_fetch_msgs": auto_fetch_msgs,
            "save_path": Path(save_path).expanduser()
            # TODO add setting to chose preview headers fetched by Inbox
            # "headers_in_preview": headers_in_preview
        }
        self.inboxes: list[Inbox] = self._get_inboxes()
    
    def get_display_inboxes(self, max_name_length: int = 40):
        display = []

        for inbox in self.inboxes:
            display.append(
                inbox.name.ljust(max_name_length)
                + str(inbox.size).rjust(6)
            )

        return display

    def _get_inboxes(self) -> list[Inbox]:
        res, inboxes = self.conn.list()
        check_res(res)

        parsed_inboxes: list[Inbox] = []

        for inbox in inboxes:
            flags, delimiter, name = (item.strip() for item
                in inbox.decode().split('"') if item.strip())

            flags = [flag.strip(" ()") for flag in flags.split("\\")
                if flag.strip(" ()")]
            if "Noselect" in flags:
                continue

            parsed_inboxes.append(Inbox(flags, delimiter, name, self,
                                        **self.inbox_data))

        # make "inbox" appear on top
        parsed_inboxes.sort(
            key=lambda inbox : 0 if "inbox" in inbox.name.lower() else 1
        )

        return parsed_inboxes

    def move_message(self, message: Msg, source_inbox: Inbox,
                     target_inbox_index: int) -> bool:
        """Returns False and skips IMAP operation if the target inbox is at the
        same time the source inbox, otherwise does a MOVE or COPY command and
        returns True"""

        target_inbox = self.inboxes[target_inbox_index]
        if target_inbox is source_inbox:
            return False
        
        capabilities = parse_list_response(self.conn.capability())
        new_uid = target_inbox.next_uid()
        message.inbox.select()

        if "MOVE" in capabilities:
            self.conn.uid("MOVE", message.uid, target_inbox.name)
            message.inbox.messages.remove(message)
        else:
            self.conn.uid("COPY", message.uid, target_inbox.name)
            message.delete()

        copyuid_response = self.conn.untagged_responses.get("COPYUID", None)
        if copyuid_response:
            new_uid = parse_list(copyuid_response)[-1]

        target_inbox.update_size()
        source_inbox.update_size()

        message.uid = new_uid
        message.inbox = target_inbox
        target_inbox.messages.append(message)

        return True

    def search(self, key, query_string, inbox_indexes):
        res, data = self.conn.search(None, key, f'"{query_string}"')
        check_res(res)
        return data
