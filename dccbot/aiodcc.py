import irc.client
import irc.connection
import irc.client_aio
from jaraco.stream import buffer
import logging


log = logging.getLogger(__name__)


class DCCProtocol(irc.client_aio.IrcProtocol):
    """Subclass of IrcProtocol handling DCC connections.

    Note that unlike IrcProtocol, this class does not implement the
    connection_made callback, as the connection is made by the user
    when calling the dcc method of the reactor.
    """


class AioDCCConnection(irc.client.DCCConnection):
    """A subclass of DCCConnection that handles DCC connections with asyncio.

    Attributes:
        reactor: The reactor that created this object.
        buffer_class: The buffer class to use for incoming data.
        passive: Whether this is a passive connection.
        peeraddress: The address of the peer.
        peerport: The port of the peer.

    """

    reactor: "AioReactor"
    buffer_class = buffer.DecodingLineBuffer

    protocol_class = DCCProtocol
    transport: irc.connection.AioFactory
    protocol: DCCProtocol
    socket: None
    connected: bool
    peeraddress: str
    peerport: int

    async def connect(self, address: str, port: int, connect_factory: irc.connection.AioFactory = irc.connection.AioFactory()):
        """Connect/reconnect to a DCC peer.

        Args:
            address: The address of the peer.
            port: The port to connect to.
            connect_factory: A callable that takes the event loop and the
              server address, and returns a connection (with a socket interface)

        Returns:
            The DCCConnection object.

        """
        self.peeraddress = address
        self.peerport = port
        self.handlers = {}
        self.buffer = self.buffer_class()

        self.connect_factory = connect_factory
        protocol_instance = self.protocol_class(self, self.reactor.loop)
        try:
            connection = self.connect_factory(protocol_instance, (self.peeraddress, self.peerport))
            transport, protocol = await connection
        except Exception as e:
            log.error("Connection error to %s:%s: %s", self.peeraddress, self.peerport, e)
            self.connected = False
            return self

        self.transport = transport
        self.protocol = protocol

        self.connected = True
        self.reactor._on_connect(self.protocol, self.transport)
        return self

    # TODO: implement listen() in asyncio way
    async def listen(self, addr=None):
        """Wait for a connection/reconnection from a DCC peer.

        Returns the DCCConnection object.

        The local IP address and port are available as
        self.peeraddress and self.peerport.
        """
        raise NotImplementedError()

    def disconnect(self, message: str = ""):
        """Hang up the connection and close the object.

        Args:
            message: Quit message.

        """
        try:
            del self.connected
        except AttributeError:
            return

        self.transport.close()

        self.reactor._handle_event(self, irc.client.Event("dcc_disconnect", self.peeraddress, "", [message]))
        self.reactor._remove_connection(self)

    def process_data(self, new_data: bytes):
        """Handle incoming data from the `DCCProtocol` connection.

        Args:
            new_data: The data to process.

        """
        if self.passive and not self.connected:
            raise NotImplementedError()
            # TODO: implement passive DCC connection

        if self.dcctype == "chat":
            self.buffer.feed(new_data)

            chunks = list(self.buffer)

            if len(self.buffer) > 2**14:
                # Bad peer! Naughty peer!
                log.info("Received >16k from a peer without a newline; disconnecting.")
                self.disconnect()
                return
        else:
            chunks = [new_data]

        command = "dccmsg"
        prefix = self.peeraddress
        target = None
        for chunk in chunks:
            log.debug("FROM PEER: %s", chunk)
            arguments = [chunk]
            log.debug(
                "command: %s, source: %s, target: %s, arguments: %s",
                command,
                prefix,
                target,
                arguments,
            )
            event = irc.client.Event(command, prefix, target, arguments)
            self.reactor._handle_event(self, event)

    def send_bytes(self, bytes: bytes):
        """Send data to DCC peer.

        Args:
            bytes: The data to send.

        """
        try:
            self.transport.write(bytes)
            log.debug("TO PEER: %r\n", bytes)
        except OSError:
            self.disconnect("Connection reset by peer.")


class AioReactor(irc.client_aio.AioReactor):
    """Asynchronous IRC client reactor with DCC support.

    This reactor is used by the IRC client to handle incoming and outgoing
    data. It also provides the ability to create DCC connections.

    Attributes:
        dcc_connection_class: The class to use for creating DCCConnection
            objects. Defaults to AioDCCConnection.

    """

    dcc_connection_class = AioDCCConnection

    def dcc(self, dcctype="chat"):
        """Create a and return a DCCConnection object.

        Args:
            dcctype (str): The type of DCC connection to create. Defaults to
                "chat". If "chat", incoming data will be split in
                newline-separated chunks. If "raw", incoming data is not
                touched.

        Returns:
            dccbot.aiodcc.AioDCCConnection: The created DCCConnection object.

        """
        with self.mutex:
            conn = self.dcc_connection_class(self, dcctype)
            self.connections.append(conn)
        return conn
