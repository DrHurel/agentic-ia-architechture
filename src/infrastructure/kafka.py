"""
Kafka implementation for message publishing and consuming.
Follows Single Responsibility Principle - handles only Kafka communication.
"""

import json
import asyncio
from typing import Callable, Optional
from confluent_kafka import Producer, Consumer, KafkaError
from confluent_kafka.admin import AdminClient, NewTopic
import structlog

from src.core.interfaces import IMessagePublisher, IMessageSubscriber
from src.core.config import KafkaSettings


logger = structlog.get_logger()


class KafkaProducer(IMessagePublisher):
    """Kafka producer implementation."""
    
    def __init__(self, settings: KafkaSettings):
        self._settings = settings
        self._producer: Optional[Producer] = None
        self._logger = logger.bind(component="KafkaProducer")
    
    def connect(self) -> None:
        """Establish connection to Kafka."""
        self._producer = Producer({
            'bootstrap.servers': self._settings.bootstrap_servers,
            'client.id': 'agentic-producer'
        })
        self._logger.info("Connected to Kafka", servers=self._settings.bootstrap_servers)
    
    def disconnect(self) -> None:
        """Disconnect from Kafka."""
        if self._producer:
            self._producer.flush()
            self._producer = None
            self._logger.info("Disconnected from Kafka")
    
    def _delivery_callback(self, err, msg):
        """Callback for message delivery confirmation."""
        if err:
            self._logger.error("Message delivery failed", error=str(err))
        else:
            self._logger.debug("Message delivered", topic=msg.topic(), partition=msg.partition())
    
    async def publish(self, topic: str, message: dict) -> None:
        """Publish a message to a Kafka topic."""
        if not self._producer:
            raise RuntimeError("Producer not connected. Call connect() first.")
        
        try:
            payload = json.dumps(message).encode('utf-8')
            self._producer.produce(
                topic,
                value=payload,
                callback=self._delivery_callback
            )
            # Trigger delivery callbacks
            self._producer.poll(0)
            self._logger.debug("Message published", topic=topic)
        except Exception as e:
            self._logger.error("Failed to publish message", topic=topic, error=str(e))
            raise


class KafkaConsumer(IMessageSubscriber):
    """Kafka consumer implementation."""
    
    def __init__(self, settings: KafkaSettings, group_id: Optional[str] = None):
        self._settings = settings
        self._group_id = group_id or settings.consumer_group
        self._consumer: Optional[Consumer] = None
        self._callbacks: dict[str, Callable] = {}
        self._running = False
        self._logger = logger.bind(component="KafkaConsumer")
    
    def connect(self) -> None:
        """Establish connection to Kafka."""
        self._consumer = Consumer({
            'bootstrap.servers': self._settings.bootstrap_servers,
            'group.id': self._group_id,
            'auto.offset.reset': self._settings.auto_offset_reset,
            'enable.auto.commit': True
        })
        self._logger.info("Connected to Kafka", servers=self._settings.bootstrap_servers, group=self._group_id)
    
    def disconnect(self) -> None:
        """Disconnect from Kafka."""
        self._running = False
        if self._consumer:
            self._consumer.close()
            self._consumer = None
            self._logger.info("Disconnected from Kafka")
    
    async def subscribe(self, topic: str, callback: Callable) -> None:
        """Subscribe to a topic with a callback handler."""
        if not self._consumer:
            raise RuntimeError("Consumer not connected. Call connect() first.")
        
        self._callbacks[topic] = callback
        topics = list(self._callbacks.keys())
        self._consumer.subscribe(topics)
        self._logger.info("Subscribed to topic", topic=topic, all_topics=topics)
    
    async def unsubscribe(self, topic: str) -> None:
        """Unsubscribe from a topic."""
        if topic in self._callbacks:
            del self._callbacks[topic]
            if self._consumer and self._callbacks:
                self._consumer.subscribe(list(self._callbacks.keys()))
            self._logger.info("Unsubscribed from topic", topic=topic)
    
    async def start_consuming(self) -> None:
        """Start consuming messages in a loop."""
        if not self._consumer:
            raise RuntimeError("Consumer not connected. Call connect() first.")
        
        self._running = True
        self._logger.info("Starting message consumption")
        
        while self._running:
            try:
                msg = self._consumer.poll(timeout=1.0)
                
                if msg is None:
                    await asyncio.sleep(0.01)  # Yield control
                    continue
                
                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    self._logger.error("Consumer error", error=str(msg.error()))
                    continue
                
                topic = msg.topic()
                if topic in self._callbacks:
                    try:
                        payload = json.loads(msg.value().decode('utf-8'))
                        await self._callbacks[topic](payload)
                    except json.JSONDecodeError as e:
                        self._logger.error("Failed to decode message", error=str(e))
                    except Exception as e:
                        self._logger.error("Callback error", topic=topic, error=str(e))
                
                await asyncio.sleep(0.01)  # Yield control
                
            except Exception as e:
                self._logger.error("Consumption error", error=str(e))
                await asyncio.sleep(1)
    
    def stop_consuming(self) -> None:
        """Stop the consumption loop."""
        self._running = False


class KafkaAdmin:
    """Kafka admin operations for topic management."""
    
    def __init__(self, settings: KafkaSettings):
        self._settings = settings
        self._admin_client: Optional[AdminClient] = None
        self._logger = logger.bind(component="KafkaAdmin")
        self._connected = False
    
    def connect(self) -> None:
        """Connect to Kafka admin."""
        self._admin_client = AdminClient({
            'bootstrap.servers': self._settings.bootstrap_servers
        })
        self._connected = True
        self._logger.info("Admin client connected")
    
    def create_topics(self, topics: list[str], num_partitions: int = 1, replication_factor: int = 1) -> None:
        """Create Kafka topics if they don't exist."""
        if not self._connected or self._admin_client is None:
            # Try reconnecting
            self._logger.warning("Admin client not connected, reconnecting...")
            self.connect()
        
        if self._admin_client is None:
            raise RuntimeError("Admin client not connected after reconnect attempt")
        
        new_topics = [
            NewTopic(topic, num_partitions=num_partitions, replication_factor=replication_factor)
            for topic in topics
        ]
        
        futures = self._admin_client.create_topics(new_topics)
        
        for topic, future in futures.items():
            try:
                future.result()
                self._logger.info("Topic created", topic=topic)
            except Exception as e:
                if "already exists" in str(e).lower():
                    self._logger.debug("Topic already exists", topic=topic)
                else:
                    self._logger.error("Failed to create topic", topic=topic, error=str(e))
