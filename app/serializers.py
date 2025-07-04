from rest_framework import serializers
from django.contrib.auth import get_user_model
from rest_framework.exceptions import ValidationError
from rest_framework.validators import UniqueValidator
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from django.db import transaction
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes
from django.urls import reverse
from django.conf import settings
from django.core.mail import send_mail

from .utils.send_mail import send_templated_email
from .models import *


User = get_user_model()


class EmailTokenObtainPairSerializer(TokenObtainPairSerializer):
    username_field = User.EMAIL_FIELD

    def validate(self, attrs):
        # Map 'email' vào 'username' để tương thích với backend
        attrs["username"] = attrs.get("email")
        return super().validate(attrs)


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "email", "role"]


class RegisterSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(
        required=True, validators=[UniqueValidator(queryset=User.objects.all())]
    )
    password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = [
            "first_name",
            "last_name",
            "phone_number",
            "email",
            "password",
        ]

    def create(self, validated_data):
        request = self.context.get("request")

        user = User.objects.create_user(
            first_name=validated_data["first_name"],
            last_name=validated_data["last_name"],
            phone_number=validated_data["phone_number"],
            email=validated_data["email"],
            username=validated_data["email"],  # Sử dụng email làm username
            password=validated_data["password"],
            role="user",  # Mặc định role là 'user'
            is_active=False,  # Tài khoản chưa kích hoạt
        )
        # Tạo token xác thực
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)

        if request:
            host = request.get_host()
        else:
            host = "localhost:8000"

        activation_link = f"http://{host}/api/activate/{uid}/{token}/"

        send_templated_email(
            subject="Kích hoạt tài khoản",
            to_email=user.email,
            template_name="emails/activation_email.html",
            context={
                "username": user.get_full_name() or user.username,
                "activation_link": activation_link,
            },
            from_email=settings.DEFAULT_FROM_EMAIL,
        )
        return user


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(required=True)
    new_password = serializers.CharField(required=True)

    def validate_new_password(self, value):
        if len(value) < 8:
            raise serializers.ValidationError("Mật khẩu phải dài ít nhất 8 ký tự.")
        return value


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    token = serializers.CharField()
    new_password = serializers.CharField()

    def validate_new_password(self, value):
        if len(value) < 8:
            raise serializers.ValidationError("Mật khẩu phải dài ít nhất 8 ký tự.")
        return value


class GenreSerializer(serializers.ModelSerializer):
    class Meta:
        model = Genre
        fields = ["id", "name"]


class ActorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Actor
        fields = ["id", "name"]


class MovieSerializer(serializers.ModelSerializer):
    actors = ActorSerializer(many=True, read_only=True)
    genres = GenreSerializer(many=True, read_only=True)

    class Meta:
        model = Movie
        fields = "__all__"


class CinemaSerializer(serializers.ModelSerializer):
    number_of_rooms = serializers.IntegerField(read_only=True)

    class Meta:
        model = Cinema
        fields = [
            'id',
            'name',
            'street',
            'ward',
            'district',
            'city',
            'number_of_rooms'
        ]


class RoomSerializer(serializers.ModelSerializer):
    cinema_id = serializers.PrimaryKeyRelatedField(
        queryset=Cinema.objects.all(),
    )

    total_seats = serializers.IntegerField(read_only=True)

    class Meta:
        model = Room
        fields = ["id", "cinema_id", "name", "no_row", "no_column", "total_seats"]

    def create(self, validated_data):
        cinema = validated_data.pop("cinema_id")
        room = Room.objects.create(cinema=cinema, **validated_data)
        room.create_seats()  # gọi hàm sinh ghế sau khi tạo room
        return room

    def update(self, instance, validated_data):
        cinema = validated_data.pop("cinema_id", None)
        if cinema is not None:
            instance.cinema = cinema

        instance.name = validated_data.get("name", instance.name)
        instance.no_row = validated_data.get("no_row", instance.no_row)
        instance.no_column = validated_data.get("no_column", instance.no_column)
        instance.save()

        # Có thể thêm logic reset ghế nếu cần
        return instance

class ShowtimeSerializer(serializers.ModelSerializer):
    movie = MovieSerializer(read_only=True)
    room = RoomSerializer(read_only=True)

    class Meta:
        model = Showtime
        fields = "__all__"


class SeatTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = SeatType
        fields = "__all__"

class RoomSeatSerializer(serializers.ModelSerializer):
    seat_type_id = serializers.PrimaryKeyRelatedField(
        source='seat_type',
        queryset=Seat.seat_type.field.related_model.objects.all(),
        required=False,
        allow_null=True
    )
    seat_type_name = serializers.CharField(source='seat_type.name', read_only=True)

    class Meta:
        model = Seat
        fields = [
            'id',
            'room',           # ID phòng (room_id)
            'seat_row',
            'seat_col',
            'seat_type_id',
            'seat_type_name',
        ]
        read_only_fields = ['id', 'room', 'seat_type_name']

class SeatSerializer(serializers.ModelSerializer):
    SEAT_STATUS_CHOICES = (
        ("available", "Available"),  # Ghế còn trống
        ("reserved", "Reserved"),  # Ghế đã đặt bởi người dùng khác
        ("hold", "Hold"),  # Ghế đang giữ bởi người dùng khác
        ("selected", "Selected"),  # Ghế đã được chọn bởi người dùng hiện tại
        ("unavailable", "Unavailable"),
    )
    seat_type = SeatTypeSerializer(read_only=True)
    status = serializers.ChoiceField(
        choices=SEAT_STATUS_CHOICES,
        default="available",
    )

    class Meta:
        model = Seat
        fields = "__all__"


class BookingSeatSerializer(serializers.ModelSerializer):
    seat = SeatSerializer(read_only=True)

    class Meta:
        model = BookingSeat
        fields = ["id", "booking", "seat"]


class BookingSerializer(serializers.ModelSerializer):
    showtime = serializers.PrimaryKeyRelatedField(
        queryset=Showtime.objects.all(), write_only=True
    )    
    booking_seats = BookingSeatSerializer(many=True, read_only=True)

    class Meta:
        model = Booking
        fields = ["id", "user", "showtime", "status", "expired_at", "booking_seats"]
        read_only_fields = ["id", "user", "status", "expired_at", "booking_seats"]

class BookingDetailSerializer(serializers.ModelSerializer):
    showtime = ShowtimeSerializer(read_only=True)

    class Meta:
        model = Booking
        fields = ['id', 'showtime', 'status', 'expired_at', 'total_amount']

class ReviewSerializer(serializers.ModelSerializer):
    author = UserSerializer(read_only=True)
    movie = serializers.PrimaryKeyRelatedField(queryset=Movie.objects.all())

    class Meta:
        model = Review
        fields = ["id", "author", "rating", "movie", "comment", "date"]
        read_only_fields = ["id", "author", "date"]


class SingleSeatBookingSerializer(serializers.Serializer):
    seat_id = serializers.IntegerField()