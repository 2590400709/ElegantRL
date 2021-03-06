from AgentRun import *
from AgentZoo import *
from AgentNet import *

"""
GAE
2    1.87e+05    215.43 |   37.06      2.67 |  189.52     -0.07      0.06  # expR > 100
2    3.67e+05    307.46 |  221.76     16.09 |  516.27     -0.10      0.16  # evaR > 300
"""


def test_conv2d():
    state_dim = (4, 96, 96)
    batch_size = 3

    def idx_dim(i):
        return int(8 * 1.5 ** i)

    mid_dim = 128
    net = nn.Sequential(
        NnnReshape(*state_dim),  # -> [batch_size, 4, 96, 96]
        nn.Conv2d(state_dim[0], idx_dim(0), 4, 2, bias=True), nn.LeakyReLU(),
        nn.Conv2d(idx_dim(0), idx_dim(1), 3, 2, bias=False), nn.ReLU(),
        nn.Conv2d(idx_dim(1), idx_dim(2), 3, 2, bias=False), nn.ReLU(),
        nn.Conv2d(idx_dim(2), idx_dim(3), 3, 2, bias=True), nn.ReLU(),
        nn.Conv2d(idx_dim(3), idx_dim(4), 3, 1, bias=True), nn.ReLU(),
        nn.Conv2d(idx_dim(4), idx_dim(5), 3, 1, bias=True), nn.ReLU(),
        NnnReshape(-1),
        nn.Linear(idx_dim(5), mid_dim), nn.ReLU(),
    )

    inp_shape = list(state_dim)
    inp_shape.insert(0, batch_size)
    inp = torch.ones(inp_shape, dtype=torch.float32)
    inp = inp.view(3, -1)
    print(inp.shape)
    out = net(inp)
    print(out.shape)
    exit()


class ActorPPO(nn.Module):
    def __init__(self, state_dim, action_dim, mid_dim):
        super().__init__()

        def idx_dim(i):
            return int(8 * 1.5 ** i)

        self.net__mean = nn.Sequential(
            NnnReshape(*state_dim),  # -> [batch_size, 4, 96, 96]
            nn.Conv2d(state_dim[0], idx_dim(0), 4, 2, bias=True), nn.LeakyReLU(),
            nn.Conv2d(idx_dim(0), idx_dim(1), 3, 2, bias=False), nn.ReLU(),
            nn.Conv2d(idx_dim(1), idx_dim(2), 3, 2, bias=False), nn.ReLU(),
            nn.Conv2d(idx_dim(2), idx_dim(3), 3, 2, bias=True), nn.ReLU(),
            nn.Conv2d(idx_dim(3), idx_dim(4), 3, 1, bias=True), nn.ReLU(),
            nn.Conv2d(idx_dim(4), idx_dim(5), 3, 1, bias=True), nn.ReLU(),
            NnnReshape(-1),
            nn.Linear(idx_dim(5), mid_dim), nn.ReLU(),
            nn.Linear(mid_dim, action_dim),
        )
        self.net__std_log = nn.Parameter(torch.zeros(1, action_dim) - 0.5, requires_grad=True)
        self.constant_log_sqrt_2pi = np.log(np.sqrt(2 * np.pi))

        # layer_norm(self.net__mean[0], std=1.0)
        # layer_norm(self.net__mean[2], std=1.0)
        layer_norm(self.net__mean[-1], std=0.01)  # output layer for action

    def forward(self, s):
        a_mean = self.net__mean(s)
        return a_mean.tanh()

    def get__a__log_prob(self, state):  # todo plan to cancel
        a_mean = self.net__mean(state)
        a_log_std = self.net__std_log.expand_as(a_mean)
        a_std = torch.exp(a_log_std)
        a_noise = torch.normal(a_mean, a_std)

        a_delta = (a_noise - a_mean).pow(2) / (2 * a_std.pow(2))
        log_prob = -(a_delta + a_log_std + self.constant_log_sqrt_2pi)
        log_prob = log_prob.sum(1)
        return a_noise, log_prob

    def compute__log_prob(self, state, a_noise):  # todo may plan to cancel
        a_mean = self.net__mean(state)
        a_log_std = self.net__std_log.expand_as(a_mean)
        a_std = torch.exp(a_log_std)

        a_delta = (a_noise - a_mean).pow(2) / (2 * a_std.pow(2))
        log_prob = -(a_delta + a_log_std + self.constant_log_sqrt_2pi)
        return log_prob.sum(1)


class CriticAdv(nn.Module):  # 2020-05-05 fix bug
    def __init__(self, state_dim, mid_dim):
        super().__init__()

        def idx_dim(i):
            return int(8 * 1.5 ** i)

        self.net = nn.Sequential(
            NnnReshape(*state_dim),  # -> [batch_size, 4, 96, 96]
            nn.Conv2d(state_dim[0], idx_dim(0), 4, 2, bias=True), nn.LeakyReLU(),
            nn.Conv2d(idx_dim(0), idx_dim(1), 3, 2, bias=False), nn.ReLU(),
            nn.Conv2d(idx_dim(1), idx_dim(2), 3, 2, bias=False), nn.ReLU(),
            nn.Conv2d(idx_dim(2), idx_dim(3), 3, 2, bias=True), nn.ReLU(),
            nn.Conv2d(idx_dim(3), idx_dim(4), 3, 1, bias=True), nn.ReLU(),
            nn.Conv2d(idx_dim(4), idx_dim(5), 3, 1, bias=True), nn.ReLU(),
            NnnReshape(-1),
            nn.Linear(idx_dim(5), mid_dim), nn.ReLU(),
            nn.Linear(mid_dim, 1),
        )

        # layer_norm(self.net[0], std=1.0)
        # layer_norm(self.net[2], std=1.0)
        layer_norm(self.net[-1], std=1.0)  # output layer for action

    def forward(self, s):
        q = self.net(s)
        return q


class AgentOffPPO:
    def __init__(self, state_dim, action_dim, net_dim):
        self.learning_rate = 1e-4  # learning rate of actor
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        '''network'''
        self.act = ActorPPO(state_dim, action_dim, net_dim).to(self.device)
        self.act.train()
        self.act_optimizer = torch.optim.Adam(self.act.parameters(), lr=self.learning_rate, )  # betas=(0.5, 0.99))
        # self.act_optimizer = torch.optim.SGD(self.act.parameters(), momentum=0.9, lr=self.learning_rate, )

        self.cri = CriticAdv(state_dim, net_dim).to(self.device)
        self.cri.train()
        self.cri_optimizer = torch.optim.Adam(self.cri.parameters(), lr=self.learning_rate, )  # betas=(0.5, 0.99))
        # self.cri_optimizer = torch.optim.SGD(self.cri.parameters(), momentum=0.9, lr=self.learning_rate, )

        self.criterion = nn.SmoothL1Loss()

    def update_buffer(self, env, buffer, max_step, reward_scale, gamma):
        rewards = list()
        steps = list()

        noise_std = np.exp(self.act.net__std_log.cpu().data.numpy()[0])
        noise_dim = noise_std.shape[0]
        # assert noise_std.shape = (action_dim, )

        step_counter = 0
        max_memo = buffer.max_len - max_step
        while step_counter < max_memo:
            reward_sum = 0
            step_sum = 0

            state = env.reset()
            for step_sum in range(max_step):
                states = torch.tensor((state,), dtype=torch.float32, device=self.device)
                a_mean = self.act.net__mean(states).cpu().data.numpy()[0]  # todo fix bug
                noise = rd.randn(noise_dim)
                action = a_mean + noise * noise_std  # todo pure_noise

                next_state, reward, done, _ = env.step(np.tanh(action))
                reward_sum += reward

                mask = 0.0 if done else gamma

                reward_ = reward * reward_scale
                buffer.append_memo((reward_, mask, state, action, noise))

                if done:
                    break

                state = next_state

            rewards.append(reward_sum)
            steps.append(step_sum)

            step_counter += step_sum
        return rewards, steps

    def update_policy(self, buffer, _max_step, batch_size, repeat_times):
        buffer.update_pointer_before_sample()

        self.act.train()
        self.cri.train()
        clip = 0.25  # ratio.clamp(1 - clip, 1 + clip)
        lambda_adv = 0.98  # why 0.98? cannot use 0.99
        lambda_entropy = 0.01  # could be 0.02
        # repeat_times = 8 could be 2**3 ~ 2**5

        actor_loss = critic_loss = None  # just for print

        '''the batch for training'''
        max_memo = buffer.now_len

        all_reward, all_mask, all_state, all_action, all_noise = buffer.all_sample(self.device)

        b_size = 2 ** 10
        with torch.no_grad():
            a_log_std = self.act.net__std_log

            all__new_v = torch.cat([self.cri(all_state[i:i + b_size])
                                    for i in range(0, all_state.size()[0], b_size)], dim=0)
            all_log_prob = torch.cat([-(all_noise[i:i + b_size].pow(2) / 2 + a_log_std).sum(1)
                                      for i in range(0, all_state.size()[0], b_size)], dim=0)

        '''compute old_v (old policy value), adv_v (advantage value) 
        refer: GAE. ICLR 2016. Generalization Advantage Estimate. 
        https://arxiv.org/pdf/1506.02438.pdf'''
        all__delta = torch.empty(max_memo, dtype=torch.float32, device=self.device)
        all__old_v = torch.empty(max_memo, dtype=torch.float32, device=self.device)  # old policy value
        all__adv_v = torch.empty(max_memo, dtype=torch.float32, device=self.device)  # advantage value
        prev_old_v = 0  # old q value
        prev_new_v = 0  # new q value
        prev_adv_v = 0  # advantage q value
        for i in range(max_memo - 1, -1, -1):
            all__delta[i] = all_reward[i] + all_mask[i] * prev_new_v - all__new_v[i]
            all__old_v[i] = all_reward[i] + all_mask[i] * prev_old_v
            all__adv_v[i] = all__delta[i] + all_mask[i] * prev_adv_v * lambda_adv
            prev_old_v = all__old_v[i]
            prev_new_v = all__new_v[i]
            prev_adv_v = all__adv_v[i]
        all__adv_v = (all__adv_v - all__adv_v.mean()) / (all__adv_v.std() + 1e-5)  # todo cancel value_norm
        # Q_value_norm is necessary. Because actor_loss = surrogate_obj + loss_entropy * lambda_entropy.

        '''mini batch sample'''
        all_old_value_std = all__old_v.std() + 1e-5
        all__old_v = all__old_v.unsqueeze(1)
        sample_times = int(repeat_times * max_memo / batch_size)

        for _ in range(sample_times):
            '''random sample'''
            indices = rd.randint(max_memo, size=batch_size)

            state = all_state[indices]
            advantage = all__adv_v[indices]
            old_value = all__old_v[indices]
            action = all_action[indices]
            old_log_prob = all_log_prob[indices]

            '''critic_loss'''
            new_value = self.cri(state)
            critic_loss = self.criterion(new_value, old_value)

            self.cri_optimizer.zero_grad()
            (critic_loss / all_old_value_std).backward()
            self.cri_optimizer.step()

            '''actor_loss'''
            a_mean = self.act.net__mean(state)  # todo fix bug
            a_log_std = self.act.net__std_log.expand_as(a_mean)
            a_std = a_log_std.exp()
            new_log_prob = -(((a_mean - action) / a_std).pow(2) / 2 + a_log_std).sum(1)

            # surrogate objective of TRPO
            ratio = torch.exp(new_log_prob - old_log_prob)
            surrogate_obj0 = advantage * ratio
            surrogate_obj1 = advantage * ratio.clamp(1 - clip, 1 + clip)
            surrogate_obj = -torch.min(surrogate_obj0, surrogate_obj1).mean()
            # policy entropy
            loss_entropy = (torch.exp(new_log_prob) * new_log_prob).mean()

            actor_loss = surrogate_obj + loss_entropy * lambda_entropy
            self.act_optimizer.zero_grad()
            actor_loss.backward()
            self.act_optimizer.step()

        self.act.eval()
        self.cri.eval()
        buffer.empty_memories_before_explore()
        # return actor_loss.item(), critic_loss.item()
        return self.act.net__std_log.mean().item(), critic_loss.item()  # todo

    def save_or_load_model(self, cwd, if_save):  # 2020-05-20
        act_save_path = '{}/actor.pth'.format(cwd)
        cri_save_path = '{}/critic.pth'.format(cwd)
        has_cri = 'cri' in dir(self)

        def load_torch_file(network, save_path):
            network_dict = torch.load(save_path, map_location=lambda storage, loc: storage)
            network.load_state_dict(network_dict)

        if if_save:
            torch.save(self.act.state_dict(), act_save_path)
            torch.save(self.cri.state_dict(), cri_save_path) if has_cri else None
            # print("Saved act and cri:", mod_dir)
        elif os.path.exists(act_save_path):
            load_torch_file(self.act, act_save_path)
            load_torch_file(self.cri, cri_save_path) if has_cri else None
        else:
            print("FileNotFound when load_model: {}".format(cwd))


"""run"""


class CarRacingEnv:
    """
    Environment wrapper for CarRacing
    refer: https://github.com/xtma/pytorch_car_caring
    """

    def __init__(self, img_stack=4, action_repeat=8):
        self.env = gym.make('CarRacing-v0')
        self.reward_threshold = self.env.spec.reward_threshold

        self.img_stack = img_stack
        self.action_repeat = action_repeat
        self.count = 0

    def reset(self):
        self.counter = 0
        self.av_r = self.reward_memory()

        self.die = False
        img_rgb = self.env.reset()
        img_gray = self.rgb2gray(img_rgb)
        self.stack = [img_gray] * self.img_stack  # four frames for decision
        # return np.array(self.stack)
        return np.array(self.stack).flatten()  # todo flatten

    def step(self, action):
        total_reward = 0

        done = None
        action = action.copy()
        action[1:] = (action[1:] + 1) / 2  # fix action_space.low
        img_rgb, reward, die, _ = self.env.step(action)
        for i in range(self.action_repeat):
            # don't penalize "die state"
            if die:
                reward += 100
            # green penalty
            if np.mean(img_rgb[:, :, 1]) > 185.0:
                reward -= 0.05
            total_reward += reward
            # if no reward recently, end the episode
            done = True if self.av_r(reward) <= -0.1 else False
            if done or die:
                break
            img_rgb, reward, die, _ = self.env.step(action)

        img_gray = self.rgb2gray(img_rgb)
        self.stack.pop(0)
        self.stack.append(img_gray)
        # assert len(self.stack) == self.img_stack
        # return np.array(self.stack), total_reward, done, die
        return np.array(self.stack).flatten(), total_reward, done, die  # todo flatten

    def render(self, *arg):
        self.env.render(*arg)

    @staticmethod
    def rgb2gray(rgb, norm=True):
        # rgb image -> gray [0, 1]
        gray = np.dot(rgb[..., :], [0.299, 0.587, 0.114])
        if norm:
            # normalize
            gray = gray / 128. - 1.
        return gray

    @staticmethod
    def reward_memory():
        # record reward for last 100 steps
        count = 0
        length = 100
        history = np.zeros(length)

        def memory(reward):
            nonlocal count
            history[count] = reward
            count = (count + 1) % length
            return np.mean(history)

        return memory


def fix_car_racing_v0_1111(env, frame_num=3, action_num=3):  # plan todo CarRacing-v0
    env.old_step = env.step
    """
    comment 'car_racing.py' line 233-234: print('Track generation ...
    comment 'car_racing.py' line 308-309: print("retry to generate track ...
    """

    def rgb2gray(rgb):
        # # rgb image -> gray [0, 1]
        # gray = np.dot(rgb[..., :], [0.299, 0.587, 0.114]).astype(np.float32)
        # if norm:
        #     # normalize
        #     gray = gray / 128. - 1.
        # return gray
        state = rgb[:, :, 1]  # show green
        # state[86:, :24] = 0  # shield speed
        state[86:, 24:36] = rgb[86:, 24:36, 2]  # show red
        state[86:, 72:] = rgb[86:, 72:, 0]  # show blue
        state = (state - 128) / 128.
        return state

    env.state_stack = None
    env.avg_reward = 0
    env.state = None

    def decorator_step(env_step):
        def new_env_step(action):
            action = action.copy()
            action[1:] = (action[1:] + 1) / 2  # fix action_space.low

            reward_sum = 0
            done = info = None
            try:
                for _ in range(action_num):
                    env.state, reward, done, info = env_step(action)

                    if done:  # don't penalize "die state"
                        reward += 100
                    if env.state.mean() > 192:  # 185.0:  # penalize when outside of road
                        reward -= 0.05

                    env.avg_reward = env.avg_reward * 0.95 + reward * 0.05
                    if env.avg_reward <= -0.1:
                        done = True

                    reward_sum += reward

                    if done:
                        break
            except Exception as error:
                print(f"| CarRacing-v0 Error b'stack underflow'?: {error}")
                reward_sum = 0
                done = True
            env.state_stack.pop(0)
            env.state_stack.append(rgb2gray(env.state))

            return np.array(env.state_stack).flatten(), reward_sum, done, info

        return new_env_step

    env.step = decorator_step(env.step)

    def decorator_reset(env_reset):
        def new_env_reset():
            state = rgb2gray(env_reset())
            env.state_stack = [state, ] * frame_num
            return np.array(env.state_stack).flatten()

        return new_env_reset

    env.reset = decorator_reset(env.reset)
    return env


def test_car_racing():
    env_name = 'CarRacing-v0'
    env, state_dim, action_dim, target_reward, is_discrete = build_gym_env(env_name, if_print=True)

    _state = env.reset()
    import cv2
    action = np.array((0, 1.0, -1.0))
    for i in range(321):
        # action = env.action_space.sample()
        state, reward, done, _ = env.step(action)
        # env.render
        show = state.reshape(state_dim)
        show = ((show[0] + 1.0) * 128).astype(np.uint8)
        cv2.imshow('', show)
        cv2.waitKey(1)
        if done:
            break
        # env.render()


def run__car_racing(gpu_id=None, random_seed=0):
    print('pixel-level state')

    args = Arguments(rl_agent=AgentOffPPO, gpu_id=gpu_id)
    args.env_name = "CarRacing-v0"
    args.random_seed = 1943 + random_seed
    args.break_step = int(2e6 * 1)
    args.max_memo = 2 ** 11
    args.batch_size = 2 ** 7
    args.repeat_times = 2 ** 4
    args.net_dim = 2 ** 7
    args.max_step = 2 ** 10
    args.eval_times2 = 2
    args.eval_times2 = 3
    args.reward_scale = 2 ** -2
    args.show_gap = 2 ** 8  # for Recorder
    args.init_for_training()
    # train_agent(**vars(args))
    train_agent_mp(args)  # train_agent(**vars(args))


if __name__ == '__main__':
    # test_conv2d()
    # test_car_racing()
    run__car_racing()
